"""
pc_video_track.py

功能：
- 屏幕抓取 -> 本地分段录制 -> 使用 ffmpeg stdin 推 RTMP 或生成 HLS
- 保留心跳检查（/api/heartbeat/{ip}）和文件上传 (/api/upload/{ip}）
- 自动重连：当服务端不允许或 ffmpeg 断开后自动重连
- 可打包：支持 PyInstaller 内置 ffmpeg（get_ffmpeg_path）

依赖：
pip install mss numpy pillow opencv-python requests

使用：
python pc_video_track.py --file_server http://8.134.173.118:5001 --mode rtmp --rtmp_server rtmp://8.134.173.118:1935 --fps 15
python pc_video_track.py --file_server http://8.134.173.118:5001 --mode hls --hls_dir ./hls_stream --fps 15
"""
import argparse
import asyncio
import os
import socket
import subprocess
import sys
import threading
import time
import datetime
import shutil
import uuid
from typing import Optional, Tuple
import logging
from logging.handlers import TimedRotatingFileHandler

import numpy as np
import requests
from mss import mss
from PIL import Image
import cv2

# -----------------------
# 配置 / 常量
# -----------------------
RECORD_DIR_DEFAULT = "./records"
LOG_DIR_DEFAULT = "./logs"
SEGMENT_MINUTES_DEFAULT = 30  # 每段录制长度（分钟）
FFMPEG_RELATIVE_DIR = "ffmpeg/bin"  # PyInstaller 打包时放置 ffmpeg/* 到这个目录

# 全局配置状态 (由心跳线程更新)
CLIENT_CONFIG = {
    "upload_enabled": False,
    "webrtc_direct": False
}

# -----------------------
# 日志配置
# -----------------------
class LoggerWriter:
    """将 stdout/stderr 重定向到 logging"""
    def __init__(self, logger, level):
        self.logger = logger
        self.level = level
        self.buffer = ""

    def write(self, message):
        if message.strip():  # 忽略空行
            self.logger.log(self.level, message.strip())
    
    def flush(self):
        pass

def setup_logging(log_dir: str = LOG_DIR_DEFAULT):
    """配置日志：按天轮转，保留7天，同时输出到控制台"""
    os.makedirs(log_dir, exist_ok=True)
    
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # 格式
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    
    # 1. 文件处理器 (按天轮转，保留7天)
    log_file = os.path.join(log_dir, "client.log")
    file_handler = TimedRotatingFileHandler(
        log_file, 
        when='midnight', 
        interval=1, 
        backupCount=7, 
        encoding='utf-8'
    )
    file_handler.suffix = "%Y-%m-%d" # 文件名后缀 client.log.2023-11-25
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # 2. 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # 3. 重定向 print 到 logger
    # 注意：重定向后，print 会变成 INFO 级别日志
    # 为了避免重复输出（因为 console_handler 已经输出了），
    # 我们只重定向 stdout 到一个不带 StreamHandler 的 logger 或者是自定义处理。
    # 但由于 print 本身是写 stdout，如果 logger 也写 stdout，会死循环。
    # 解决方案：移除 console_handler，或者让 LoggerWriter 只写文件？
    # 不，通常的做法是：保留 print 用于控制台，额外加一行代码记录到 logger。
    # 但用户想要"print的内容写到日志"。
    
    # 修正方案：
    # 既然要保留控制台显示，又要有文件日志。
    # 我们不重定向 sys.stdout，因为那样太复杂且容易死循环。
    # 我们定义一个 log_print 函数替换内置 print？工作量大。
    # 
    # 更好的方案：
    # 使用 logging.basicConfig 配置 file handler。
    # 然后定义一个辅助函数 `log(...)` 替代 `print(...)`。
    # 但为了兼容现有代码，我将重写 sys.stdout.write。
    
    # 简单粗暴且有效的方法：
    # 劫持 sys.stdout，写入文件的同时，写入 原始的 sys.__stdout__
    
    sys.stdout = DualWriter(sys.stdout, file_handler)
    sys.stderr = DualWriter(sys.stderr, file_handler)

class DualWriter:
    """同时写入 原始stdout 和 日志文件"""
    def __init__(self, original_stream, file_handler):
        self.original_stream = original_stream
        self.file_handler = file_handler
        # 创建一个只包含 file_handler 的 logger 用于记录 print 内容
        self.logger = logging.getLogger("print_logger")
        self.logger.addHandler(file_handler)
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False # 防止冒泡给 root logger 导致 console 重复

    def write(self, message):
        # 写回控制台
        self.original_stream.write(message)
        self.original_stream.flush()
        
        # 写入文件 (去除多余换行，因为 logging 会自动加换行)
        if message.strip():
            record = logging.LogRecord(
                name="print", level=logging.INFO, pathname="", lineno=0,
                msg=message.strip(), args=(), exc_info=None
            )
            self.file_handler.emit(record)

    def flush(self):
        self.original_stream.flush()


# -----------------------
# 文件清理管理器
# -----------------------
class FileCleanupManager:
    def __init__(self, record_dir: str, retention_minutes: int = 5):
        self.record_dir = record_dir
        self.retention_minutes = retention_minutes
        self._stop_event = threading.Event()
        
    def start(self):
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()
        print(f"[CLEANUP] thread started. Scanning {self.record_dir} every 60s.")
        
    def _loop(self):
        # 初始启动时先执行一次清理（处理上次运行残留）
        self.clean_old_files()
        
        while not self._stop_event.is_set():
            time.sleep(60) # 每分钟扫描一次
            self.clean_old_files()
            
    def clean_old_files(self):
        if not os.path.exists(self.record_dir):
            return
            
        now = time.time()
        retention_sec = self.retention_minutes * 60
        
        for fname in os.listdir(self.record_dir):
            if not fname.endswith(".mp4"):
                continue
                
            fpath = os.path.join(self.record_dir, fname)
            try:
                mtime = os.path.getmtime(fpath)
                # 如果文件修改时间超过保留时间（防止删除正在录制的）
                if now - mtime > retention_sec:
                    try:
                        os.remove(fpath)
                        print(f"[CLEANUP] removed stale file: {fname}")
                    except OSError:
                        # 文件可能被占用（上传中），跳过
                        pass
            except Exception as e:
                print(f"[CLEANUP] error checking {fname}: {e}")

# -----------------------
# 工具函数
# -----------------------
def get_ip_address() -> str:
    """获取主机 IPv4 地址（用于生成唯一的流 key）"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # 连接一个公共地址，不会真的发送数据
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "unknown"


def get_device_id() -> str:
    """获取或生成持久化的设备 UUID"""
    id_file = "device_id.txt"
    # 确定存储目录（兼容打包环境）
    base_dir = os.path.dirname(os.path.abspath(__file__))
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    
    path = os.path.join(base_dir, id_file)
    
    # 尝试读取现有 ID
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                content = f.read().strip()
                if content:
                    return content
        except:
            pass
            
    # 生成新 ID 并保存
    new_id = str(uuid.uuid4())
    try:
        with open(path, "w") as f:
            f.write(new_id)
    except Exception as e:
        print(f"[CONFIG] Failed to save device_id: {e}")
        
    return new_id


def get_ffmpeg_path() -> str:
    """
    获取 ffmpeg 可执行文件路径。
    支持两种运行场景：
    - 源码运行：项目根目录下的 ffmpeg/ffmpeg.exe
    - PyInstaller 打包后：sys._MEIPASS/ffmpeg/ffmpeg.exe
    """
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    exe_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    path = os.path.join(base_path, FFMPEG_RELATIVE_DIR, exe_name)
    if not os.path.exists(path):
        # 兜底，尝试全局 PATH 下的 ffmpeg
        return "ffmpeg"
    return path


def check_single_instance(port=40006):
    """
    检查是否已有实例在运行（通过绑定本地端口实现）。
    如果端口绑定失败，说明已有实例，直接退出程序。
    返回 socket 对象，需保持引用。
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(('127.0.0.1', port))
        return s
    except OSError:
        print(f"[STARTUP] Another instance is already running (port {port} is busy). Exiting...")
        time.sleep(2)
        sys.exit(0)


def create_startup_shortcut():
    """
    创建 Windows 开机自启动快捷方式
    """
    try:
        # Windows 启动文件夹路径
        startup_folder = os.path.join(os.getenv('APPDATA'), r'Microsoft\Windows\Start Menu\Programs\Startup')
        shortcut_name = "CatchScreenClient.lnk"
        shortcut_path = os.path.join(startup_folder, shortcut_name)

        # 确定目标和参数
        if getattr(sys, 'frozen', False):
            # PyInstaller 打包后的 exe
            target_path = sys.executable
            arguments = "" 
            working_dir = os.path.dirname(sys.executable)
        else:
            # 源码运行 python xxx.py
            target_path = sys.executable # python.exe
            script_path = os.path.abspath(__file__)
            args_list = [script_path] + sys.argv[1:]
            arguments = " ".join(f'"{a}"' if " " in a else a for a in args_list)
            working_dir = os.path.dirname(script_path)

        print(f"[STARTUP] Creating shortcut at: {shortcut_path}")
        print(f"[STARTUP] Target: {target_path}")

        # 使用 VBScript 创建快捷方式
        vbs_content = f'''
        Set oWS = WScript.CreateObject("WScript.Shell")
        sLinkFile = "{shortcut_path}"
        Set oLink = oWS.CreateShortcut(sLinkFile)
        oLink.TargetPath = "{target_path}"
        oLink.Arguments = "{arguments}"
        oLink.WorkingDirectory = "{working_dir}"
        oLink.Description = "CatchScreen Client Auto Start"
        oLink.Save
        '''
        
        vbs_file = os.path.join(working_dir, "create_shortcut.vbs")
        with open(vbs_file, "w", encoding="ansi") as f:
            f.write(vbs_content)
        
        subprocess.run(["cscript", "//Nologo", vbs_file], check=True)
        
        if os.path.exists(vbs_file):
            os.remove(vbs_file)
            
        print("[STARTUP] Successfully added to Windows Startup.")
        
    except Exception as e:
        print(f"[STARTUP] Error setting up startup: {e}")


# -----------------------
# 与后端交互（心跳 / 上传）
# -----------------------
def start_heartbeat_thread(server_url: str):
    """启动后台心跳线程，定期更新 CLIENT_CONFIG"""
    def loop():
        print(f"[HEARTBEAT] thread started, target: {server_url}")
        while True:
            try:
                # 直接调用 API 获取最新状态，而不是通过 get_client_state (因为它有 fallback)
                ip = get_ip_address()
                device_id = get_device_id()
                # 添加 device_id 参数
                url = f"{server_url.rstrip('/')}/api/heartbeat/{ip}?device_id={device_id}"
                
                try:
                    resp = requests.get(url, timeout=5)
                    if resp.status_code == 200:
                        j = resp.json()
                        print(j)
                        upload_enabled = bool(j.get("upload_enabled", False))
                        webrtc_direct = bool(j.get("webrtc_direct", False))
                        
                        # 详细日志
                        print(f"[HEARTBEAT] {ip} -> upload={upload_enabled}, direct={webrtc_direct}")
                        
                        # 状态变更日志
                        if upload_enabled != CLIENT_CONFIG["upload_enabled"]:
                            print(f"[CONFIG] upload_enabled changed: {CLIENT_CONFIG['upload_enabled']} -> {upload_enabled}")
                        if webrtc_direct != CLIENT_CONFIG["webrtc_direct"]:
                            print(f"[CONFIG] webrtc_direct changed: {CLIENT_CONFIG['webrtc_direct']} -> {webrtc_direct}")
                        
                        CLIENT_CONFIG["upload_enabled"] = upload_enabled
                        CLIENT_CONFIG["webrtc_direct"] = webrtc_direct
                    else:
                        print(f"[HEARTBEAT] server returned {resp.status_code}")
                except Exception as e:
                    print(f"[HEARTBEAT] request failed: {e}")
                    
            except Exception as e:
                print(f"[HEARTBEAT] loop error: {e}")
            
            time.sleep(60) # 每 60 秒心跳一次

    t = threading.Thread(target=loop, daemon=True)
    t.start()


def get_client_state(server_url: str) -> Tuple[bool, bool]:
    """
    查询后端是否允许上传与直接推流。
    (保留此函数作为手动调用的接口，虽然现在主要靠心跳线程更新全局配置)
    """
    return CLIENT_CONFIG["upload_enabled"], CLIENT_CONFIG["webrtc_direct"]


def upload_to_server(server_url: str, filepath: str) -> bool:
    """
    上传文件到后端（POST multipart/form-data）
    """
    ip = get_ip_address()
    url = server_url.rstrip("/") + "/api/upload/" + ip
    for _ in range(3):
        try:
            with open(filepath, "rb") as f:
                files = {"file": f}
                resp = requests.post(url, files=files, timeout=30)
                if resp.status_code == 200:
                    print(f"[UPLOAD] success: {filepath}")
                    # 尝试立即删除本地文件，如果失败则由后台清理线程处理
                    try:
                        os.remove(filepath)
                        print(f"[UPLOAD] deleted local file: {filepath}")
                    except Exception as e:
                        print(f"[UPLOAD] delete postponed (locked): {filepath}")
                    return True
                else:
                    print(f"[UPLOAD] server returned {resp.status_code}: {resp.text}")
        except Exception as e:
            print(f"[UPLOAD] exception uploading {filepath}: {e}")
        time.sleep(1)
    print(f"[UPLOAD] failed after retries: {filepath}")
    return False


# -----------------------
# 本地录制（线程）
# -----------------------
class ScreenRecorder:
    """
    屏幕抓取 + 本地分段录制（使用 ffmpeg）
    录制结束后异步上传旧段。
    """
    def __init__(self, fps: int = 15, record_dir: str = RECORD_DIR_DEFAULT, segment_minutes: int = SEGMENT_MINUTES_DEFAULT, server_url: str = ""):
        self.fps = max(1, int(fps))
        self.record_dir = record_dir
        os.makedirs(self.record_dir, exist_ok=True)
        self.segment_seconds = max(10, int(segment_minutes) * 60)
        self.server_url = server_url

        # Get dimensions (temporary mss instance)
        with mss() as sct:
            try:
                monitor = sct.monitors[1] # Try primary monitor first
            except IndexError:
                monitor = sct.monitors[0]
            
            self.raw_width = monitor["width"]
            self.raw_height = monitor["height"]

        # 录制尺寸 (宽高必须是偶数，这里简单减半，也可以不减半)
        self.width = self.raw_width // 2
        self.height = self.raw_height // 2
        if self.width % 2 != 0: self.width -= 1
        if self.height % 2 != 0: self.height -= 1

        self._stop_event = threading.Event()
        self._ffmpeg_proc: Optional[subprocess.Popen] = None
        self._current_file: Optional[str] = None
        self._start_time: Optional[float] = None

        self._thread = threading.Thread(target=self._record_loop, daemon=True)
        self._thread.start()

    def _start_new_segment(self):
        """启动新的录制分段"""
        if self._ffmpeg_proc:
            self._close_segment()

        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = os.path.join(self.record_dir, f"screen_{ts}.mp4")
        
        ffmpeg_exe = get_ffmpeg_path()
        # 构建 ffmpeg 命令：读取 stdin rawvideo -> h264 mp4
        cmd = [
            ffmpeg_exe,
            "-y",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-pix_fmt", "bgra",      # mss 抓取的是 bgra
            "-s", f"{self.raw_width}x{self.raw_height}", # 输入尺寸
            "-r", str(self.fps),
            "-i", "-",               # 从 stdin 读取
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",   # 浏览器兼容必须
            "-preset", "ultrafast",  # 录制追求低 CPU
            "-vf", f"scale={self.width}:{self.height}", # 缩放
            "-movflags", "+faststart", # 边下边播优化
            fname
        ]
        
        # 隐藏窗口 (Windows)
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        try:
            self._ffmpeg_proc = subprocess.Popen(
                cmd, 
                stdin=subprocess.PIPE, 
                stderr=subprocess.DEVNULL, # 忽略日志
                startupinfo=startupinfo
            )
            self._current_file = fname
            self._start_time = time.time()
            print(f"[REC] start new file {fname}")
        except Exception as e:
            print(f"[REC] failed to start ffmpeg: {e}")
            self._ffmpeg_proc = None

    def _close_segment(self):
        """关闭当前分段并触发上传"""
        if self._ffmpeg_proc:
            try:
                if self._ffmpeg_proc.stdin:
                    self._ffmpeg_proc.stdin.close()
                self._ffmpeg_proc.wait(timeout=5)
            except Exception as e:
                print(f"[REC] error closing ffmpeg: {e}")
                self._ffmpeg_proc.kill()
            self._ffmpeg_proc = None
        
        # 触发上传（受控于全局配置）
        if self._current_file and os.path.exists(self._current_file):
            file_size = os.path.getsize(self._current_file)
            size_mb = file_size / (1024 * 1024)
            print(f"[REC] segment finished: {self._current_file} (Size: {size_mb:.2f} MB)")
            
            if self.server_url:
                if CLIENT_CONFIG["upload_enabled"]:
                    filepath = self._current_file
                    threading.Thread(target=upload_to_server, args=(self.server_url, filepath), daemon=True).start()
                else:
                    print(f"[REC] upload skipped (upload_enabled=False): {self._current_file}")
        
        self._current_file = None

    def stop(self):
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join()
        self._close_segment()

    def _record_loop(self):
        print(f"[REC] started. Res: {self.width}x{self.height}, FPS: {self.fps}")
        
        # self._start_new_segment()
        
        # 在线程内部初始化 mss，确保线程安全
        with mss() as sct:
            # 获取监视器配置
            try:
                monitor = sct.monitors[1]
            except IndexError:
                monitor = sct.monitors[0]
                
            while not self._stop_event.is_set():
                # 1. 检查录制开关 (upload_enabled)
                if not CLIENT_CONFIG["upload_enabled"]:
                    if self._ffmpeg_proc:
                        print("[REC] upload_enabled=False, stopping recording...")
                        self._close_segment()
                    time.sleep(1)
                    continue

                # 2. 如果开启了但未录制，则开始
                if self._ffmpeg_proc is None:
                    print("[REC] upload_enabled=True, starting recording...")
                    self._start_new_segment()

                start_t = time.time()
                
                # 检查是否需要切片
                if self._start_time and (time.time() - self._start_time > self.segment_seconds):
                    self._start_new_segment()
                
                # 抓屏
                try:
                    # mss grab returns BGRA
                    sct_img = sct.grab(monitor)
                    raw_bytes = sct_img.raw
                    
                    # 写入 ffmpeg stdin
                    if self._ffmpeg_proc and self._ffmpeg_proc.stdin:
                        try:
                            self._ffmpeg_proc.stdin.write(raw_bytes)
                            self._ffmpeg_proc.stdin.flush()
                        except (BrokenPipeError, OSError):
                            # ffmpeg 可能崩溃或已关闭，重启一段
                            print("[REC] ffmpeg pipe broken, restarting segment")
                            self._start_new_segment()

                except Exception as e:
                    print(f"[REC] capture error: {e}")
                
                # 控制帧率
                elapsed = time.time() - start_t
                sleep_t = (1.0 / self.fps) - elapsed
                if sleep_t > 0:
                    time.sleep(sleep_t)

        self._close_segment()
        print("[REC] stopped")


# -----------------------
# FFmpeg 推流基类
# -----------------------
class FFmpegPublisher:
    def __init__(self, file_server: str, fps: int = 10, width: Optional[int] = None, height: Optional[int] = None, reconnect_delay: float = 5.0):
        self.file_server = file_server
        self.fps = max(1, int(fps))
        self.reconnect_delay = reconnect_delay
        
        self.sct = mss()
        try:
            mon = self.sct.monitors[1]
        except IndexError:
            mon = self.sct.monitors[0]
            
        # 自动计算分辨率：默认缩放至宽度不超过 1280
        raw_w, raw_h = mon["width"], mon["height"]
        target_w = width
        target_h = height
        
        if not target_w:
            # 如果原图太宽，强制缩放到 1280 以下
            scale = 1.0
            if raw_w > 1280:
                scale = 1280 / raw_w
            # 或者默认减半
            elif raw_w > 800:
                scale = 0.5
                
            target_w = int(raw_w * scale)
            target_h = int(raw_h * scale)

        self.width = target_w
        self.height = target_h
        
        # Make sure dims are even for x264
        if self.width % 2 != 0: self.width -= 1
        if self.height % 2 != 0: self.height -= 1
        
        self.client_ip = get_ip_address()
        self._stop = False
        self._process: Optional[subprocess.Popen] = None
        
    def _build_cmd(self):
        raise NotImplementedError
        
    def _start_ffmpeg(self):
        cmd = self._build_cmd()
        # print(f"[FFMPEG] start: {' '.join(cmd[:6])} ...")
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        self._process = p
        return p
        
    async def run(self):
        loop = asyncio.get_event_loop()
        
        while not self._stop:
            try:
                # 检查是否允许推流 (由心跳线程更新)
                if not CLIENT_CONFIG["webrtc_direct"]:
                    # print("[STREAM] streaming disabled (webrtc_direct=False), waiting...")
                    await asyncio.sleep(2.0)
                    continue

                proc = self._start_ffmpeg()
                if not proc or not proc.stdin:
                    print("[STREAM] ffmpeg not started properly, retry later")
                    await asyncio.sleep(self.reconnect_delay)
                    continue

                print("[STREAM] pushing started")
                try:
                    # Use try/except block around the grab loop
                    with mss() as sct:
                        try:
                            mon = sct.monitors[1]
                        except IndexError:
                            mon = sct.monitors[0]

                        frame_count = 0
                        total_bytes_sent = 0
                        last_stats_time = time.time()

                        while True:
                            # 检查是否需要中断推流
                            if not CLIENT_CONFIG["webrtc_direct"]:
                                print("[STREAM] stopped by server (webrtc_direct=False)")
                                break
                            
                            t0 = time.time()
                            img = sct.grab(mon)
                            # ... (raw conversion logic same as before)
                            arr = Image.frombytes("RGB", img.size, img.rgb) 
                            frame = np.array(arr)
                            frame = cv2.resize(frame, (self.width, self.height))
                            raw_out = frame.tobytes()
                            
                            try:
                                proc.stdin.write(raw_out)
                                total_bytes_sent += len(raw_out)
                                # proc.stdin.flush() 
                            except BrokenPipeError:
                                print("[FFMPEG] BrokenPipeError -> ffmpeg exited")
                                break
                            except Exception as e:
                                print(f"[FFMPEG] write exception: {e}")
                                break
                            
                            # Print stats every 5 seconds
                            if time.time() - last_stats_time >= 5.0:
                                rate_mb = (total_bytes_sent / (1024 * 1024)) / (time.time() - last_stats_time)
                                print(f"[STREAM] pushing rate: {rate_mb:.2f} MB/s (Raw Video Input)")
                                total_bytes_sent = 0
                                last_stats_time = time.time()

                            elapsed = time.time() - t0
                            sleep_for = max(0, (1.0 / self.fps) - elapsed)
                            if sleep_for > 0:
                                await asyncio.sleep(sleep_for)
                finally:
                    try:
                        if proc.stdin: proc.stdin.close()
                    except: pass
                    try:
                        proc.wait(timeout=2)
                    except:
                        try: proc.kill()
                        except: pass
                    print("[STREAM] ffmpeg stopped")

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[STREAM] exception in run loop: {e}")
            
            await asyncio.sleep(self.reconnect_delay)

    def stop(self):
        self._stop = True
        try:
            if self._process:
                self._process.kill()
        except Exception:
            pass


class FFmpegRTMPPublisher(FFmpegPublisher):
    """
    使用 ffmpeg stdin 推 raw frames 到 RTMP（flv）
    """
    def __init__(self, file_server: str, rtmp_server: str, **kwargs):
        super().__init__(file_server, **kwargs)
        self.rtmp_server = rtmp_server.rstrip("/")
        
        if self.rtmp_server.startswith("rtmp://") or self.rtmp_server.startswith("rtmps://"):
            self.rtmp_url = f"{self.rtmp_server}/live/{self.client_ip}"
        else:
            self.rtmp_url = f"rtmp://{self.rtmp_server}/live/{self.client_ip}"

    def _build_cmd(self):
        ffmpeg = get_ffmpeg_path()
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel", "error",
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            "-s", f"{self.width}x{self.height}",
            "-r", str(self.fps),
            "-i", "-",  # stdin
            "-c:v", "libx264",
            "-preset", "ultrafast",  # 优化：ultrafast 降低 CPU
            "-tune", "zerolatency",
            "-pix_fmt", "yuv420p",
            "-g", str(max(2, self.fps * 2)),
            "-f", "flv",
            self.rtmp_url
        ]
        print(f"[FFMPEG-RTMP] target: {self.rtmp_url}")
        return cmd


class FFmpegHLSPublisher(FFmpegPublisher):
    """
    使用 ffmpeg stdin 推 raw frames 到本地 HLS 文件
    """
    def __init__(self, file_server: str, hls_dir: str, **kwargs):
        super().__init__(file_server, **kwargs)
        self.hls_dir = hls_dir
        safe_ip = self.client_ip.replace("/", "_")
        self.output_dir = os.path.join(self.hls_dir, safe_ip)
        os.makedirs(self.output_dir, exist_ok=True)
        self.playlist_file = os.path.join(self.output_dir, "index.m3u8")
        
    def _build_cmd(self):
        ffmpeg = get_ffmpeg_path()
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel", "error",
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            "-s", f"{self.width}x{self.height}",
            "-r", str(self.fps),
            "-i", "-",  # stdin
            "-c:v", "libx264",
            "-preset", "ultrafast", # 优化：ultrafast 降低 CPU
            "-tune", "zerolatency",
            "-pix_fmt", "yuv420p",
            "-g", str(max(2, self.fps * 2)),
            "-f", "hls",
            "-hls_time", "2",
            "-hls_list_size", "5",
            "-hls_flags", "delete_segments",
            self.playlist_file
        ]
        print(f"[FFMPEG-HLS] target: {self.playlist_file}")
        return cmd


# -----------------------
# 程序入口
async def main_async(args):
    # 启动心跳线程
    start_heartbeat_thread(args.file_server)
    
    # 启动文件清理管理器 (每分钟清理一次超过60分钟的残留文件)
    cleanup_mgr = FileCleanupManager(record_dir=args.record_dir, retention_minutes=60)
    cleanup_mgr.start()
    
    # start recorder thread (record & upload)
    recorder = ScreenRecorder(fps=args.fps, record_dir=args.record_dir, segment_minutes=args.segment_minutes, server_url=args.file_server)

    publisher = None
    if args.mode == "rtmp":
        publisher = FFmpegRTMPPublisher(
            file_server=args.file_server,
            rtmp_server=args.rtmp_server,
            fps=args.fps,
            reconnect_delay=args.reconnect_delay
        )
    elif args.mode == "hls":
        # HLS mode: ensure directory exists
        if not args.hls_dir:
            # Default to local folder compatible with backend default
            default_hls = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hls_stream")
            args.hls_dir = default_hls
            
        publisher = FFmpegHLSPublisher(
            file_server=args.file_server,
            hls_dir=args.hls_dir,
            fps=args.fps,
            reconnect_delay=args.reconnect_delay
        )
    else:
        print(f"Unknown mode: {args.mode}")
        return

    try:
        await publisher.run()
    except KeyboardInterrupt:
        print("[MAIN] KeyboardInterrupt, stopping")
    finally:
        if publisher: publisher.stop()
        recorder.stop()


def main():
    # 1. 单实例检查
    _instance_sock = check_single_instance()

    setup_logging()
    
    parser = argparse.ArgumentParser(description="Screen -> RTMP/HLS Publisher")
    parser.add_argument("--file_server", default="http://8.134.173.118:5001", help="file server base URL")
    parser.add_argument("--mode", default="rtmp", choices=["rtmp", "hls"], help="streaming mode: rtmp or hls")
    parser.add_argument("--rtmp_server", default="rtmp://8.134.173.118:1935", help="rtmp server (rtmp://host:port)")
    parser.add_argument("--hls_dir", default="", help="local directory to save HLS files (mode=hls)")
    parser.add_argument("--fps", type=int, default=5, help="frame per second")
    parser.add_argument("--record-dir", default=RECORD_DIR_DEFAULT, help="local record directory")
    parser.add_argument("--segment-minutes", type=int, default=SEGMENT_MINUTES_DEFAULT, help="record segment minutes")
    parser.add_argument("--reconnect-delay", type=float, default=5.0, help="reconnect delay seconds")
    args = parser.parse_args()

    # 如果是打包后的 exe，自动注册开机自启
    if getattr(sys, 'frozen', False):
        print("[MAIN] Running as executable, ensuring startup shortcut exists...")
        create_startup_shortcut()

    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("[MAIN] exit")


if __name__ == "__main__":
    main()
