"""
屏幕抓取 WebRTC 发布客户端
-------------------------
功能：
- 抓取本机屏幕（默认全屏，支持设置帧率）
- 通过 WebRTC 与服务端（webrtc_server.py 的 /webrtc）协商并发布视频轨

依赖安装：
    pip install aiortc mss av numpy

启动示例：
    python screen_publisher.py --server http://127.0.0.1:8080 --fps 15

说明：
- 服务端需使用本项目提供的 webrtc_server.py，并已启动 8080 端口。
- 该客户端仅发布视频（屏幕流），不发布音频。
"""

import argparse
import asyncio
import time
from typing import Optional

import numpy as np
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import VideoStreamTrack
from av import VideoFrame
from mss import mss

import os
import datetime
import requests
import socket
import aiohttp
import cv2
import threading
from PIL import Image

# log = CustomLogger(log_file="logs/app.log")

def get_ip_address():
    """获取当前主机的IP地址"""
    try:
        # 尝试连接一个外部服务器，获取本地IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip_address = s.getsockname()[0]
        s.close()
        return ip_address
    except Exception:
        return "127.0.0.1"  # 如果失败，默认返回localhost

def get_client_state(server_url:str) -> Optional[bool]:
    """查询发布状态（用户实现）"""
    url = server_url.rstrip('/') + "/api/heartbeat/" + get_ip_address()
    print(f"[UPLOAD] 查询发布状态 URL: {url}")
    for _ in range(3):  # 最多重试两次
        try:
            response = requests.get(url)
            print(f"[UPLOAD] 查询发布状态: {response}")
            if response.status_code == 200:
                print(f"获取状态成功: {response.json()}")
                json_text = response.json()
                return json_text.get("upload_enabled", False), json_text.get("webrtc_direct", False)

        except Exception as e:
            with open("recorder_error.log", "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now()}] ERROR: {e}\n")
            print(f"[ERROR] 查询发布状态 时发生错误: {e}")
            # return False
    return False, False


def upload_to_server(server_url: str, filepath: str):
    """上传逻辑（用户实现）"""

    url = server_url.rstrip('/') + "/api/upload/" + get_ip_address()
    for _ in range(3):  # 最多重试两次
        try:
            with open(filepath, 'rb') as f:
                files = {'file': f}
                response = requests.post(url, files=files)
                if response.status_code == 200:
                    print(f"[UPLOAD] 上传成功: {filepath}")
                    return True
        except Exception as e:
            with open("recorder_error.log", "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now()}] ERROR: {e}\n")
            print(f"[ERROR] 上传文件 {filepath} 时发生错误: {e}")
            # return False

class ScreenShareTrack(VideoStreamTrack):
    """持续屏幕录制 + aiortc 推流（每5分钟自动上传）"""
    def __init__(
        self,
        fps: int = 15,
        monitor_index: int = 0,
        record_dir: str = "./records",
        segment_minutes: int = 5,
        codec: str = "avc1",
        server_url: str = "http://"
    ):
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
        self._frame_queue = asyncio.Queue(maxsize=100)
        self._stop_event = threading.Event()
        self._current_writer = None
        self._current_start_time = None

        # # 启动录制线程
        self._record_thread = threading.Thread(target=self._record_loop, daemon=True)
        self._record_thread.start()

        # # 启动上传线程
        # self._upload_thread = threading.Thread(target=self._upload_scheduler, daemon=True)
        # self._upload_thread.start()

    def _new_video_writer(self):
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

    def _record_loop(self):
        """独立线程：持续从队列取帧写入文件"""
        self._new_video_writer()
        sct = mss()
        monitor = sct.monitors[0]  # 全屏（所有显示器）
        # print(self._stop_event.is_set())
        while True:
            try:
                img = sct.grab(monitor)
                frame = Image.frombytes('RGB', img.size, img.rgb)
                frame_np = np.array(frame)
                
                frame_bgr = cv2.cvtColor(frame_np, cv2.COLOR_RGB2BGR)
                # 降低分辨率，宽高缩小一半
                frame_bgr = cv2.resize(frame_bgr, (monitor['width']//2, monitor['height']//2))
                self._current_writer.write(frame_bgr)

                # 检查是否超过时间段，切换新文件
                if time.time() - self._current_start_time > self.segment_seconds:
                # print(f"[DEBUG] 检查录制时间 {time.time() - self._current_start_time}")
                # if time.time() - self._current_start_time > 5:  # 测试用，1分钟改为5秒
                    old_file = self._current_file
                    self._current_writer.release()
                    self._new_video_writer()
                    # 启动异步上传旧文件
                    print(f"[INFO] 启动上传线程上传文件: {old_file}")
                    threading.Thread(target=upload_to_server, args=(self.server_url, old_file,), daemon=True).start()

            except Exception as e:
                print("[ERROR] 录制线程异常，继续...", e)
                continue

    # def _upload_scheduler(self):
    #     """备用上传守护线程（确保文件没丢）"""
    #     while not self._stop_event.is_set():
    #         time.sleep(self.segment_seconds + 10)
    #         # 检查是否有未上传的旧文件
    #         for f in os.listdir(self.record_dir):
    #             if f.endswith(".mp4") and "uploading" not in f:
    #                 path = os.path.join(self.record_dir, f)
    #                 # 异步上传
    #                 print(f"[INFO] 启动上传线程上传文件: {path}")
    #                 threading.Thread(target=upload_to_server, args=(self.server_url, path,), daemon=True).start()
    
    async def recv(self) -> VideoFrame:
        """推流帧获取"""
        # 抓屏
        img = np.array(self._sct.grab(self._mon))  # (H, W, 4)
        frame_rgb = img[:, :, :3][:, :, ::-1]

        # 推流帧
        frame = VideoFrame.from_ndarray(frame_rgb, format="rgb24")
        pts, time_base = await self.next_timestamp()
        frame.pts, frame.time_base = pts, time_base
        return frame

    # def stop(self):
    #     """优雅停止录制"""
    #     self._stop_event.set()
    #     if self._current_writer:
    #         self._current_writer.release()
    #     print("[INFO] 屏幕录制已停止。")


class video_publisher():
    def __init__(self, file_server_ip: str, rtc_server_ip: str, fps: int, reconnect_delay: float = 3.0):
        self.file_server_ip = file_server_ip
        self.rtc_server_ip = rtc_server_ip
        self.fps = fps
        self.reconnect_delay = reconnect_delay
        self.screen_track = ScreenShareTrack(fps=15, monitor_index = 0, segment_minutes=1, server_url=file_server_ip)       

    def start(self):
        while True:
            try:
                asyncio.run(self.publish_screen())
            except KeyboardInterrupt:
                break

    async def publish_screen(self) -> None:
        publish_url = f"{self.rtc_server_ip.rstrip('/')}/webrtc"

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            while True:
                upload_enabled, publish_enabled = get_client_state(self.file_server_ip)
                print(f"[webrtc] upload_enabled={upload_enabled}, publish_enabled={publish_enabled}")
                if publish_enabled == False:
                    print("[webrtc] 服务端不允许发布，等待重试...")
                    await asyncio.sleep(max(1.0, 10))
                    continue
                pc = RTCPeerConnection()

                # 添加屏幕视频轨
                # screen_track = ScreenShareTrack(fps=self.fps)
                pc.addTrack(self.screen_track)

                # 连接状态日志
                self.done_event: asyncio.Event = asyncio.Event()

                @pc.on("connectionstatechange")
                def on_state_change():
                    state = pc.connectionState
                    print(f"[webrtc] connectionState -> {state}")
                    if state in ("failed", "closed", "disconnected"):
                        try:
                            self.done_event.set()
                        except Exception:
                            pass

                try:
                    # 创建 Offer 并设置本地描述
                    offer = await pc.createOffer()
                    await pc.setLocalDescription(offer)

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

                    # 持续运行，直到连接断开或失败
                    await self.done_event.wait()
                    print("⚠️ 连接已断开，准备重连...")

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    print(f"❌ 发布失败：{e}")
                finally:
                    try:
                        await asyncio.wait_for(pc.close(), timeout=3)
                    except asyncio.TimeoutError:
                        print("⚠️ pc.close() 超时，强制跳过。")
                    except Exception as e:
                        print(f"⚠️ pc.close() 异常：{e}")
                    else:
                        print("✅ PeerConnection 已关闭。")

                # 重连等待
                await asyncio.sleep(max(0.5, self.reconnect_delay))
                print("🔄 准备重新发布屏幕流...")



def main():
    parser = argparse.ArgumentParser(description="屏幕抓取 WebRTC 发布客户端")
    parser.add_argument("--file_server", default="http://127.0.0.1:5001", help="服务端基础地址，如 http://127.0.0.1:8080")
    parser.add_argument("--trc_server", default="http://127.0.0.1:5002", help="服务端基础地址，如 http://127.0.0.1:8080")
    parser.add_argument("--fps", type=int, default=15, help="抓取帧率，默认 15 fps")
    parser.add_argument("--reconnect-delay", type=float, default=3.0, help="断开后的重连等待秒数，默认 3.0s")
    args = parser.parse_args()


    video_publisher_instance = video_publisher(
        file_server_ip=args.file_server,
        rtc_server_ip=args.trc_server,
        fps=args.fps,
        reconnect_delay=args.reconnect_delay,
    )

    video_publisher_instance.start()
    # time.sleep(100)

    # print(get_client_state("http://172.16.0.195:5000"))

    # 启动屏幕分享并录制到本地
    # track = ScreenShareTrack(fps=20, record=True, output_file="output.mp4")

    # # 在 aiortc PeerConnection 中使用
    # pc.addTrack(track)

    # # 停止时调用
    # await track.stop_recording()



if __name__ == "__main__":
    main()


