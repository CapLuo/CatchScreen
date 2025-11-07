"""
屏幕抓取 WebRTC 发布客户端
-------------------------
功能：
- 抓取本机屏幕（默认全屏，支持设置帧率）
- 通过 WebRTC 与服务端协商并发布视频轨
- 自动重连机制：检测到连接断开后自动重连
- 服务端未启动时循环检测，成功后继续推流

依赖安装：
    pip install aiortc mss av numpy opencv-python pillow requests aiohttp

启动示例：
    python pc_video_track.py --file_server http://127.0.0.1:5001 --rtc_server http://127.0.0.1:5002 --fps 15
"""
import argparse
import asyncio
import time
import os
import datetime
import socket
import threading
from typing import Optional, Tuple

import numpy as np
import requests
import aiohttp
import cv2
from PIL import Image
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import VideoStreamTrack
from av import VideoFrame
from mss import mss


def get_ip_address() -> str:
    """获取当前主机的IP地址"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip_address = s.getsockname()[0]
        s.close()
        return ip_address
    except Exception:
        return "127.0.0.1"


def get_client_state(server_url: str) -> Tuple[bool, bool]:
    """
    查询客户端状态（上传和推流权限）
    
    Args:
        server_url: 后端服务器地址
        
    Returns:
        (upload_enabled, webrtc_direct) 元组
    """
    url = server_url.rstrip('/') + "/api/heartbeat/" + get_ip_address()
    print(f"[UPLOAD] 查询发布状态 URL: {url}")
    for _ in range(3):
        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                json_text = response.json()
                upload_enabled = json_text.get("upload_enabled", False)
                webrtc_direct = json_text.get("webrtc_direct", False)
                print(f"[UPLOAD] 获取状态成功: upload_enabled={upload_enabled}, webrtc_direct={webrtc_direct}")
                return upload_enabled, webrtc_direct
        except Exception as e:
            print(f"[ERROR] 查询发布状态时发生错误: {e}")
    return False, False


def upload_to_server(server_url: str, filepath: str) -> bool:
    """
    上传文件到服务器
    
    Args:
        server_url: 后端服务器地址
        filepath: 要上传的文件路径
        
    Returns:
        是否上传成功
    """
    url = server_url.rstrip('/') + "/api/upload/" + get_ip_address()
    for _ in range(3):
        try:
            with open(filepath, 'rb') as f:
                files = {'file': f}
                response = requests.post(url, files=files, timeout=30)
                if response.status_code == 200:
                    print(f"[UPLOAD] 上传成功: {filepath}")
                    return True
        except Exception as e:
            print(f"[ERROR] 上传文件 {filepath} 时发生错误: {e}")
    return False


class ScreenShareTrack(VideoStreamTrack):
    """
    屏幕分享视频轨，支持本地录制和 WebRTC 推流
    """
    def __init__(
        self,
        fps: int = 15,
        monitor_index: int = 0,
        record_dir: str = "./records",
        segment_minutes: int = 5,
        codec: str = "avc1",
        server_url: str = "http://127.0.0.1:5001"
    ) -> None:
        """
        初始化屏幕分享轨
        
        Args:
            fps: 帧率
            monitor_index: 显示器索引
            record_dir: 录制文件保存目录
            segment_minutes: 每个录制片段时长（分钟）
            codec: 视频编码器
            server_url: 后端服务器地址
        """
        super().__init__()
        self.fps = max(1, min(60, int(fps)))
        self._frame_interval = 1.0 / self.fps
        self._last_ts = 0.0
        self._sct = mss()
        self._mon = self._sct.monitors[monitor_index]
        self.codec = codec
        self.server_url = server_url

        # 录制配置
        self.record_dir = record_dir
        os.makedirs(record_dir, exist_ok=True)
        self.segment_minutes = segment_minutes
        self.segment_seconds = segment_minutes * 60

        # 控制变量
        self._stop_event = threading.Event()
        self._current_writer: Optional[cv2.VideoWriter] = None
        self._current_start_time: Optional[float] = None
        self._current_file: Optional[str] = None

        # 启动录制线程
        self._record_thread = threading.Thread(target=self._record_loop, daemon=True)
        self._record_thread.start()

    def _new_video_writer(self) -> cv2.VideoWriter:
        """开始一个新的本地录制文件"""
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = os.path.join(self.record_dir, f"screen_{ts}.mp4")
        width, height = self._mon["width"] // 2, self._mon["height"] // 2
        fourcc = cv2.VideoWriter_fourcc(*self.codec)
        out = cv2.VideoWriter(filename, fourcc, self.fps, (width, height))
        self._current_writer = out
        self._current_start_time = time.time()
        self._current_file = filename
        print(f"[INFO] 开始录制新文件: {filename}")
        return out

    def _record_loop(self) -> None:
        """独立线程：持续从队列取帧写入文件"""
        self._new_video_writer()
        sct = mss()
        monitor = sct.monitors[0]
        
        while not self._stop_event.is_set():
            try:
                img = sct.grab(monitor)
                frame = Image.frombytes('RGB', img.size, img.rgb)
                frame_np = np.array(frame)
                frame_bgr = cv2.cvtColor(frame_np, cv2.COLOR_RGB2BGR)
                frame_bgr = cv2.resize(frame_bgr, (monitor['width']//2, monitor['height']//2))
                self._current_writer.write(frame_bgr)

                # 检查是否超过时间段，切换新文件
                if time.time() - self._current_start_time > self.segment_seconds:
                    old_file = self._current_file
                    self._current_writer.release()
                    self._new_video_writer()
                    # 启动异步上传旧文件
                    print(f"[INFO] 启动上传线程上传文件: {old_file}")
                    threading.Thread(target=upload_to_server, args=(self.server_url, old_file,), daemon=True).start()

            except Exception as e:
                print(f"[ERROR] 录制线程异常: {e}")
                continue
    
    async def recv(self) -> VideoFrame:
        """推流帧获取"""
        img = np.array(self._sct.grab(self._mon))
        frame_rgb = img[:, :, :3][:, :, ::-1]
        frame = VideoFrame.from_ndarray(frame_rgb, format="rgb24")
        pts, time_base = await self.next_timestamp()
        frame.pts, frame.time_base = pts, time_base
        return frame


class VideoPublisher:
    """
    WebRTC 视频发布器，支持自动重连
    """
    def __init__(
        self,
        file_server_ip: str,
        rtc_server_ip: str,
        fps: int = 15,
        reconnect_delay: float = 5.0,
        check_interval: float = 2.0,
        recreate_track_on_reconnect: bool = False
    ) -> None:
        """
        初始化视频发布器
        
        Args:
            file_server_ip: 后端文件服务器地址
            rtc_server_ip: WebRTC 服务器地址
            fps: 帧率
            reconnect_delay: 重连延迟（秒）
            check_interval: 状态检查间隔（秒）
            recreate_track_on_reconnect: 是否在每次重连时创建新的 track（默认 False）
                                        - False: 复用同一个 track，保持时间戳连续性，录制线程持续运行
                                        - True: 每次重连创建新 track，完全重置状态
        """
        self.file_server_ip = file_server_ip
        self.rtc_server_ip = rtc_server_ip
        self.fps = fps
        self.reconnect_delay = reconnect_delay
        self.check_interval = check_interval
        self.recreate_track_on_reconnect = recreate_track_on_reconnect
        
        # 创建屏幕分享轨
        # 注意：VideoStreamTrack 是独立的，不依赖于特定的 PeerConnection
        # 同一个 track 可以被多个 PeerConnection 使用，重连时也可以复用
        # 复用 track 的好处：
        # 1. 保持时间戳连续性（next_timestamp() 会维护时间戳）
        # 2. 录制线程持续运行，不会中断
        # 3. 避免资源浪费（不需要重新创建 mss 对象等）
        self.screen_track: Optional[ScreenShareTrack] = None
        self._create_screen_track()
    
    def _create_screen_track(self) -> None:
        """创建新的屏幕分享轨"""
        if self.screen_track is not None:
            # 停止旧的 track（如果存在）
            try:
                self.screen_track._stop_event.set()
                print("[INFO] 已停止旧的 ScreenShareTrack")
            except Exception as e:
                print(f"[WARN] 停止旧 track 时出错: {e}")
        
        self.screen_track = ScreenShareTrack(
            fps=15,
            monitor_index=0,
            segment_minutes=1,
            server_url=self.file_server_ip
        )
        print("[INFO] 已创建新的 ScreenShareTrack")

    def start(self) -> None:
        """启动发布器（阻塞）"""
        while True:
            try:
                asyncio.run(self.publish_screen())
            except KeyboardInterrupt:
                print("\n[INFO] 收到中断信号，正在退出...")
                break
            except Exception as e:
                print(f"[ERROR] 发布器异常: {e}")
                time.sleep(self.reconnect_delay)

    async def wait_for_server_ready(self) -> bool:
        """
        等待服务器准备就绪（循环检测）
        
        Returns:
            是否成功检测到服务器就绪
        """
        print("[INFO] 等待服务器准备就绪...")
        while True:
            try:
                # 检查后端服务器
                upload_enabled, webrtc_direct = get_client_state(self.file_server_ip)
                print(f"[INFO] 状态检查: upload_enabled={upload_enabled}, webrtc_direct={webrtc_direct}")
                
                if webrtc_direct:
                    print("[INFO] 服务器已准备就绪，可以推流")
                    return True
                
                # 检查 WebRTC 服务器是否可访问
                async with aiohttp.ClientSession() as session:
                    try:
                        async with session.get(f"{self.rtc_server_ip.rstrip('/')}/preview", timeout=aiohttp.ClientTimeout(total=2)) as resp:
                            if resp.status < 500:
                                print("[INFO] WebRTC 服务器可访问")
                    except Exception:
                        print("[WARN] WebRTC 服务器不可访问，继续等待...")
                
                await asyncio.sleep(self.check_interval)
                
            except KeyboardInterrupt:
                return False
            except Exception as e:
                print(f"[ERROR] 等待服务器时出错: {e}")
                await asyncio.sleep(self.check_interval)

    async def publish_screen(self) -> None:
        """
        发布屏幕流（主推流逻辑）
        支持自动重连和服务器检测
        """
        publish_url = f"{self.rtc_server_ip.rstrip('/')}/offer"
        
        # 等待服务器准备就绪
        if not await self.wait_for_server_ready():
            return

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            is_first_connection = True  # 标记是否是第一次连接
            
            while True:
                # 检查推流权限
                upload_enabled, webrtc_direct = get_client_state(self.file_server_ip)
                print(f"[webrtc] upload_enabled={upload_enabled}, webrtc_direct={webrtc_direct}")
                
                if not webrtc_direct:
                    print("[webrtc] 服务端不允许发布，等待重试...")
                    await asyncio.sleep(self.check_interval)
                    continue
                
                # 重要：每次重连都必须创建新的 RTCPeerConnection
                # 因为一旦连接状态变为 closed/failed/disconnected，旧的 PeerConnection 无法重用
                pc: Optional[RTCPeerConnection] = None
                done_event = asyncio.Event()

                try:
                    # 如果不是第一次连接，且配置了 recreate_track_on_reconnect，则创建新的 track
                    if not is_first_connection and self.recreate_track_on_reconnect:
                        self._create_screen_track()
                        print("[INFO] 已为本次重连创建新的 ScreenShareTrack")
                    
                    # 创建新的 PeerConnection
                    pc = RTCPeerConnection()
                    print("[INFO] 创建新的 RTCPeerConnection")

                    @pc.on("connectionstatechange")
                    def on_state_change():
                        state = pc.connectionState
                        print(f"[webrtc] connectionState -> {state}")
                        if state in ("failed", "closed", "disconnected"):
                            try:
                                done_event.set()
                            except Exception:
                                pass

                    # 添加屏幕视频轨
                    # 注意：同一个 VideoStreamTrack 可以被多个 PeerConnection 使用
                    # 但每次重连时，我们使用新的 PeerConnection，旧的会自动清理
                    # 默认复用同一个 track，保持时间戳连续性和录制线程持续运行
                    pc.addTrack(self.screen_track)

                    # 创建 Offer 并设置本地描述
                    offer = await pc.createOffer()
                    await pc.setLocalDescription(offer)
                    print("[INFO] 已创建 Offer，发送到服务器...")

                    # 向服务端发布，获取 Answer
                    async with session.post(
                        publish_url,
                        json={"sdp": pc.localDescription.sdp, "type": pc.localDescription.type},
                    ) as resp:
                        if resp.status != 200:
                            text = await resp.text()
                            raise RuntimeError(f"Publish failed: {resp.status} {text}")
                        
                        data = await resp.json()
                        answer_sdp = data.get("sdp")
                        answer_type = data.get("type", "answer")
                        
                        if not answer_sdp:
                            raise RuntimeError(f"Server returned no SDP: {data}")
                        
                        await pc.setRemoteDescription(RTCSessionDescription(sdp=answer_sdp, type=answer_type))
                        print("✅ 屏幕流发布成功，等待连接或服务端断开...")
                        
                        # 标记已不是第一次连接
                        is_first_connection = False

                    # 持续运行，直到连接断开或失败
                    await done_event.wait()
                    print("⚠️ 连接已断开，准备重连...")

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    print(f"❌ 发布失败：{e}")
                    # 即使失败，也标记为不是第一次连接
                    is_first_connection = False
                finally:
                    # 重要：确保旧的 PeerConnection 被正确关闭和清理
                    # 注意：不需要手动停止 track，因为同一个 VideoStreamTrack 可以被多个连接使用
                    # 关闭 PeerConnection 时会自动处理关联的 tracks
                    if pc is not None:
                        try:
                            # 关闭 PeerConnection（会自动清理关联的资源）
                            await asyncio.wait_for(pc.close(), timeout=3)
                            print("✅ PeerConnection 已关闭并清理")
                        except asyncio.TimeoutError:
                            print("⚠️ pc.close() 超时，强制跳过")
                        except Exception as e:
                            print(f"⚠️ pc.close() 异常：{e}")
                        finally:
                            # 确保引用被清除，便于垃圾回收
                            pc = None

                # 重连等待
                print(f"🔄 等待 {self.reconnect_delay} 秒后重新发布屏幕流...")
                await asyncio.sleep(self.reconnect_delay)


def main() -> None:
    """主函数"""
    parser = argparse.ArgumentParser(description="屏幕抓取 WebRTC 发布客户端")
    parser.add_argument(
        "--file_server",
        default="http://127.0.0.1:5001",
        help="后端文件服务器地址，如 http://127.0.0.1:5001"
    )
    parser.add_argument(
        "--rtc_server",
        default="http://127.0.0.1:5002",
        help="WebRTC 服务器地址，如 http://127.0.0.1:5002"
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=15,
        help="抓取帧率，默认 15 fps"
    )
    parser.add_argument(
        "--reconnect-delay",
        type=float,
        default=5.0,
        help="断开后的重连等待秒数，默认 5.0s"
    )
    parser.add_argument(
        "--check-interval",
        type=float,
        default=2.0,
        help="状态检查间隔（秒），默认 2.0s"
    )
    parser.add_argument(
        "--recreate-track",
        action="store_true",
        help="每次重连时创建新的 ScreenShareTrack（默认：复用同一个 track）"
    )
    args = parser.parse_args()

    publisher = VideoPublisher(
        file_server_ip=args.file_server,
        rtc_server_ip=args.rtc_server,
        fps=args.fps,
        reconnect_delay=args.reconnect_delay,
        check_interval=args.check_interval,
        recreate_track_on_reconnect=args.recreate_track,
    )

    publisher.start()


if __name__ == "__main__":
    main()
