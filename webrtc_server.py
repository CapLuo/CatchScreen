"""
WebRTC 推流服务器
-----------------
使用 aiohttp 提供 WebRTC 推流服务，支持多客户端连接和网页预览
"""
import asyncio
import json
import time
from datetime import datetime
from typing import Dict, Set, Optional, Tuple, Any
import os

from aiohttp import web
from aiohttp.web import Request, Response
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay
import requests

# 配置
BACKEND_BASE = os.environ.get('BACKEND_BASE', 'http://127.0.0.1:5001')
SERVER_PORT = int(os.environ.get('WEBRTC_PORT', '5002'))

# 全局状态
relay: MediaRelay = MediaRelay()
pcs: Set[RTCPeerConnection] = set()
pc_info: Dict[str, Dict] = {}  # {pc_id: {"type": str, "ip": str, "created_at": float, "remote_addr": str}}
published: Dict[str, Optional[Any]] = {"video": None, "audio": None}
VIEWER_ACTIVE: bool = False


def _log_connection(level: str, pc_id: str, msg: str, **kwargs) -> None:
    """记录连接相关日志"""
    info = pc_info.get(pc_id, {})
    conn_type = info.get("type", "unknown")
    ip = info.get("ip", "N/A")
    remote = info.get("remote_addr", "N/A")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    extra = " ".join([f"{k}={v}" for k, v in kwargs.items()])
    print(f"[{timestamp}] [{level}] [WebRTC-{conn_type.upper()}] PC#{pc_id[:8]} IP={ip} Remote={remote} | {msg} {extra}".strip())


def _setup_pc_logging(pc: RTCPeerConnection, pc_id: str, conn_type: str, ip: Optional[str] = None, remote_addr: Optional[str] = None) -> None:
    """为 PeerConnection 设置状态监听和日志"""
    pc_info[pc_id] = {
        "type": conn_type,
        "ip": ip or "N/A",
        "remote_addr": remote_addr or "N/A",
        "created_at": time.time()
    }
    
    @pc.on("connectionstatechange")
    def on_connection_state_change():
        state = pc.connectionState
        _log_connection("INFO", pc_id, f"连接状态变化: {state}")
        
        if state == "closed" or state == "failed" or state == "disconnected":
            # 清理连接
            info = pc_info.pop(pc_id, {})
            pcs.discard(pc)
            duration = time.time() - info.get("created_at", time.time())
            conn_type = info.get("type", "unknown")
            _log_connection("INFO", pc_id, f"连接已关闭 ({state}) | 持续时间: {duration:.2f}秒", duration=f"{duration:.2f}s")
            
            # 如果是发布者连接关闭，清理发布轨 todo
            if conn_type == "publisher":
                if published["video"] is not None:
                    _log_connection("INFO", pc_id, "清理发布者视频轨")
                    published["video"] = None
                if published["audio"] is not None:
                    _log_connection("INFO", pc_id, "清理发布者音频轨")
                    published["audio"] = None
    
    @pc.on("iceconnectionstatechange")
    def on_ice_state_change():
        state = pc.iceConnectionState
        _log_connection("INFO", pc_id, f"ICE 连接状态: {state}")
    
    @pc.on("signalingstatechange")
    def on_signaling_state_change():
        state = pc.signalingState
        _log_connection("DEBUG", pc_id, f"信令状态: {state}")


async def handle_offer(request: Request) -> Response:
    """
    处理客户端推流的 Offer
    POST /offer
    Body: {"sdp": "...", "type": "offer"}
    """
    try:
        # 先清理断开的连接
        await cleanup_closed_connections()
        
        payload = await request.json()
        offer_sdp = payload.get("sdp")
        offer_type = payload.get("type", "offer")
        
        if not offer_sdp:
            return web.json_response({"error": "missing sdp"}, status=400)
        
        pc = RTCPeerConnection()
        pc_id = str(id(pc))
        remote_addr = request.remote
        
        # 从请求头提取 IP
        ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or str(remote_addr)
        
        # 清理相同 IP 的旧发布者连接
        old_pcs_to_close = []
        for old_pc in list(pcs):
            old_pc_id = str(id(old_pc))
            old_info = pc_info.get(old_pc_id, {})
            if old_info.get("type") == "publisher" and old_info.get("ip") == ip:
                old_pcs_to_close.append(old_pc)
                _log_connection("INFO", old_pc_id, f"发现相同 IP ({ip}) 的旧发布者连接，准备关闭")
        
        # 关闭旧连接
        for old_pc in old_pcs_to_close:
            try:
                old_pc_id = str(id(old_pc))
                await old_pc.close()
                pcs.discard(old_pc)
                pc_info.pop(old_pc_id, None)
                _log_connection("INFO", old_pc_id, "旧发布者连接已关闭")
            except Exception as e:
                _log_connection("ERROR", str(id(old_pc)), f"关闭旧连接失败: {e}")
        
        # 如果清理了旧连接，也清理发布轨
        if old_pcs_to_close:
            published["video"] = None
            published["audio"] = None
            _log_connection("INFO", pc_id, "已清理旧发布轨")
        
        _setup_pc_logging(pc, pc_id, "publisher", ip=ip, remote_addr=str(remote_addr))
        pcs.add(pc)
        _log_connection("INFO", pc_id, f"创建发布者连接 (IP: {ip})")
        
        @pc.on("track")
        def on_track(track):
            _log_connection("INFO", pc_id, f"收到发布者媒体轨: kind={track.kind}")
            if track.kind == "video":
                published["video"] = relay.subscribe(track)
                _log_connection("INFO", pc_id, "视频轨已发布并订阅")
            elif track.kind == "audio":
                published["audio"] = relay.subscribe(track)
                _log_connection("INFO", pc_id, "音频轨已发布并订阅")
        
        # SDP 协商
        await pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type=offer_type))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        
        _log_connection("INFO", pc_id, "SDP 协商完成，返回 Answer")
        
        return web.json_response({
            "sdp": pc.localDescription.sdp,
            "type": pc.localDescription.type
        })
        
    except Exception as e:
        _log_connection("ERROR", "unknown", f"处理 Offer 失败: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_view(request: Request) -> Response:
    """
    处理网页预览的 Offer
    POST /view
    Body: {"sdp": "...", "type": "offer", "timeout": 0}
    """
    try:
        payload = await request.json()
        offer_sdp = payload.get("sdp")
        offer_type = payload.get("type", "offer")
        timeout_s = payload.get("timeout")
        
        if not offer_sdp:
            return web.json_response({"error": "missing sdp"}, status=400)
        
        pc = RTCPeerConnection()
        pc_id = str(id(pc))
        remote_addr = request.remote
        ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or str(remote_addr)
        
        _setup_pc_logging(pc, pc_id, "viewer", ip=ip, remote_addr=str(remote_addr))
        pcs.add(pc)
        _log_connection("INFO", pc_id, "创建观众连接")
        
        # 等待发布端上线
        if not (published["video"] or published["audio"]):
            _log_connection("INFO", pc_id, "等待发布端上线...")
            if timeout_s is None or float(timeout_s) <= 0:
                # 无限等待
                wait_start = time.time()
                while not (published["video"] or published["audio"]):
                    await asyncio.sleep(0.2)
                wait_duration = time.time() - wait_start
                _log_connection("INFO", pc_id, f"发布端已上线，等待耗时: {wait_duration:.2f}秒")
            else:
                deadline = time.time() + float(timeout_s)
                wait_start = time.time()
                while not (published["video"] or published["audio"]) and time.time() < deadline:
                    await asyncio.sleep(0.2)
                if not (published["video"] or published["audio"]):
                    wait_duration = time.time() - wait_start
                    _log_connection("WARN", pc_id, f"等待超时，无发布轨可用 | 等待时长: {wait_duration:.2f}秒")
                    return web.json_response({"error": "no published tracks"}, status=409)
                wait_duration = time.time() - wait_start
                _log_connection("INFO", pc_id, f"发布端已上线，等待耗时: {wait_duration:.2f}秒")
        
        # 添加已发布的轨
        tracks_added = []
        if published["video"]:
            pc.addTrack(published["video"])
            tracks_added.append("video")
        if published["audio"]:
            pc.addTrack(published["audio"])
            tracks_added.append("audio")
        _log_connection("INFO", pc_id, f"已添加媒体轨: {', '.join(tracks_added)}")
        
        # SDP 协商
        await pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type=offer_type))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        
        _log_connection("INFO", pc_id, "SDP 协商完成，返回 Answer")
        
        return web.json_response({
            "sdp": pc.localDescription.sdp,
            "type": pc.localDescription.type
        })
        
    except Exception as e:
        _log_connection("ERROR", "unknown", f"处理 View 失败: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_viewer_open(request: Request) -> Response:
    """处理观众进入通知"""
    global VIEWER_ACTIVE
    VIEWER_ACTIVE = True
    
    payload = await request.json()
    ip = payload.get("ip")
    
    if ip and ip != '-':
        try:
            requests.patch(
                f"{BACKEND_BASE}/api/folders/{ip}/webrtc_direct",
                json={"webrtc_direct": True},
                timeout=2
            )
            print(f"[viewer/open] IP={ip} | webrtc_direct 已更新为 1")
        except Exception as e:
            print(f"[viewer/open] 更新 webrtc_direct 失败: {e}")
    
    return web.json_response({"viewer": True})


async def handle_viewer_close(request: Request) -> Response:
    """处理观众离开通知，关闭所有连接"""
    global VIEWER_ACTIVE, published
    
    payload = await request.json()
    ip = payload.get("ip", "N/A")
    remote_addr = request.remote
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [INFO] [VIEWER_CLOSE] IP={ip} Remote={remote_addr} | 开始关闭所有连接")
    
    VIEWER_ACTIVE = False
    
    # 更新后端状态
    if ip and ip != '-' and ip != 'N/A':
        try:
            requests.patch(
                f"{BACKEND_BASE}/api/folders/{ip}/webrtc_direct",
                json={"webrtc_direct": False},
                timeout=2
            )
            print(f"[{timestamp}] [INFO] [VIEWER_CLOSE] IP={ip} | webrtc_direct 已更新为 0")
        except Exception as e:
            print(f"[{timestamp}] [ERROR] [VIEWER_CLOSE] IP={ip} | 更新失败: {e}")
    
    # 关闭所有连接
    closed_count = 0
    publisher_count = 0
    viewer_count = 0
    
    for pc in list(pcs):
        pc_id = str(id(pc))
        info = pc_info.get(pc_id, {})
        conn_type = info.get("type", "unknown")
        
        if conn_type == "publisher":
            publisher_count += 1
        elif conn_type == "viewer":
            viewer_count += 1
        
        try:
            current_state = pc.connectionState if hasattr(pc, 'connectionState') else "unknown"
            _log_connection("INFO", pc_id, f"主动关闭{conn_type}连接 | 当前状态: {current_state}")
            await pc.close()
            closed_count += 1
        except Exception as e:
            _log_connection("ERROR", pc_id, f"关闭连接时出错: {e}")
        finally:
            pcs.discard(pc)
            pc_info.pop(pc_id, None)
    
    # 清空发布轨
    had_video = published["video"] is not None
    had_audio = published["audio"] is not None
    published = {"video": None, "audio": None}
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [INFO] [VIEWER_CLOSE] IP={ip} | 关闭完成:")
    print(f"  - 关闭连接数: {closed_count} (发布者: {publisher_count}, 观众: {viewer_count})")
    print(f"  - 发布轨清理: video={'已清空' if had_video else '无'}, audio={'已清空' if had_audio else '无'}")
    print(f"  - 剩余连接数: {len(pcs)}")
    
    return web.json_response({"viewer": False, "closed": closed_count})


async def cleanup_closed_connections() -> None:
    """清理已关闭的连接"""
    closed_pcs = []
    for pc in list(pcs):
        pc_id = str(id(pc))
        try:
            state = pc.connectionState if hasattr(pc, 'connectionState') else "unknown"
            if state in ("closed", "failed", "disconnected"):
                closed_pcs.append((pc, pc_id))
        except Exception:
            # 如果无法获取状态，也认为连接已断开
            closed_pcs.append((pc, pc_id))
    
    # 清理断开的连接
    for pc, pc_id in closed_pcs:
        try:
            info = pc_info.pop(pc_id, {})
            pcs.discard(pc)
            conn_type = info.get("type", "unknown")
            _log_connection("INFO", pc_id, f"清理断开的{conn_type}连接")
            
            # 如果是发布者连接，清理发布轨
            if conn_type == "publisher":
                if published["video"] is not None:
                    published["video"] = None
                if published["audio"] is not None:
                    published["audio"] = None
        except Exception as e:
            _log_connection("ERROR", pc_id, f"清理连接时出错: {e}")


async def handle_status(request: Request) -> Response:
    """获取服务器状态（用于调试）"""
    # 先清理断开的连接
    await cleanup_closed_connections()
    
    publisher_count = len([p for p in pcs if pc_info.get(str(id(p)), {}).get("type") == "publisher"])
    viewer_count = len([p for p in pcs if pc_info.get(str(id(p)), {}).get("type") == "viewer"])
    
    # 检查连接状态
    active_publishers = []
    for pc in pcs:
        pc_id = str(id(pc))
        info = pc_info.get(pc_id, {})
        if info.get("type") == "publisher":
            try:
                state = pc.connectionState if hasattr(pc, 'connectionState') else "unknown"
            except Exception:
                state = "error"
            active_publishers.append({
                "pc_id": pc_id[:8],
                "ip": info.get("ip", "N/A"),
                "state": state,
                "created_at": info.get("created_at", 0)
            })
    
    return web.json_response({
        "total_connections": len(pcs),
        "publisher_count": publisher_count,
        "viewer_count": viewer_count,
        "has_video": published["video"] is not None,
        "has_audio": published["audio"] is not None,
        "viewer_active": VIEWER_ACTIVE,
        "active_publishers": active_publishers
    })


async def handle_preview(request: Request) -> Response:
    """返回预览页面 HTML"""
    html = """<!doctype html>
<html lang=zh-CN>
<head>
  <meta charset=utf-8>
  <meta name=viewport content="width=device-width,initial-scale=1">
  <title>WebRTC 实时预览</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body { background:#0b0d10; color:#eaecef; margin:0; padding:0; }
    .video-container { position: relative; width: 100%; height: 100vh; display: flex; align-items: center; justify-content: center; }
    video { width:100%; max-width:100%; max-height:100vh; border-radius: 10px; background:#111; }
    video:-webkit-full-screen { width: 100vw; height: 100vh; max-width: 100vw; max-height: 100vh; border-radius: 0; }
    video:-moz-full-screen { width: 100vw; height: 100vh; max-width: 100vw; max-height: 100vh; border-radius: 0; }
    video:-ms-fullscreen { width: 100vw; height: 100vh; max-width: 100vw; max-height: 100vh; border-radius: 0; }
    video:fullscreen { width: 100vw; height: 100vh; max-width: 100vw; max-height: 100vh; border-radius: 0; }
    .fullscreen-btn { position: absolute; top: 10px; right: 10px; z-index: 1000; background: rgba(0,0,0,0.7); color: white; border: none; padding: 8px 12px; border-radius: 5px; cursor: pointer; font-size: 18px; line-height: 1; }
    .fullscreen-btn:hover { background: rgba(0,0,0,0.9); }
    .fullscreen-btn:active { transform: scale(0.95); }
    .fullscreen-btn span { display: inline-block; }
  </style>
</head>
<body>
  <div class="video-container">
        <video id="player" autoplay playsinline controls></video>
    <button class="fullscreen-btn" id="fullscreenBtn" onclick="toggleFullscreen()" title="全屏 (F11)">
      <span id="fullscreenIcon">⛶</span>
    </button>
  </div>

  <script>
const player = document.getElementById('player');
const fullscreenBtn = document.getElementById('fullscreenBtn');
const fullscreenIcon = document.getElementById('fullscreenIcon');
    const urlParams = new URLSearchParams(location.search);
    const ip = urlParams.get('ip') || '-';

let currentPC = null;
let releaseHandler = null;

// 全屏功能
function toggleFullscreen() {
  try {
    if (!document.fullscreenElement && !document.webkitFullscreenElement && !document.mozFullScreenElement && !document.msFullscreenElement) {
      // 进入全屏 - 优先使用视频容器，如果失败则使用视频元素
      const videoContainer = player.parentElement;
      
      // 尝试容器全屏
      if (videoContainer.requestFullscreen) {
        videoContainer.requestFullscreen().catch(err => {
          console.log('容器全屏失败，尝试视频元素全屏:', err);
          // 如果容器全屏失败，尝试视频元素全屏
          requestVideoFullscreen();
        });
      } else if (videoContainer.webkitRequestFullscreen) {
        videoContainer.webkitRequestFullscreen();
      } else if (videoContainer.mozRequestFullScreen) {
        videoContainer.mozRequestFullScreen();
      } else if (videoContainer.msRequestFullscreen) {
        videoContainer.msRequestFullscreen();
      } else {
        // 直接使用视频元素全屏
        requestVideoFullscreen();
      }
    } else {
      // 退出全屏
      exitFullscreen();
    }
  } catch (error) {
    console.error('全屏操作失败:', error);
    // 如果所有方法都失败，尝试使用视频元素的全屏
    requestVideoFullscreen();
  }
}

// 视频元素全屏
function requestVideoFullscreen() {
  if (player.requestFullscreen) {
    player.requestFullscreen().catch(err => console.error('视频全屏失败:', err));
  } else if (player.webkitRequestFullscreen) {
    player.webkitRequestFullscreen();
  } else if (player.webkitEnterFullscreen) {
    player.webkitEnterFullscreen(); // iOS Safari
  } else if (player.mozRequestFullScreen) {
    player.mozRequestFullScreen();
  } else if (player.msRequestFullscreen) {
    player.msRequestFullscreen();
  } else {
    console.warn('浏览器不支持全屏API');
    alert('您的浏览器不支持全屏功能');
  }
}

// 退出全屏
function exitFullscreen() {
  if (document.exitFullscreen) {
    document.exitFullscreen().catch(err => console.error('退出全屏失败:', err));
  } else if (document.webkitExitFullscreen) {
    document.webkitExitFullscreen();
  } else if (document.mozCancelFullScreen) {
    document.mozCancelFullScreen();
  } else if (document.msExitFullscreen) {
    document.msExitFullscreen();
  }
}

// 监听全屏状态变化
function handleFullscreenChange() {
  const isFullscreen = !!(document.fullscreenElement || document.webkitFullscreenElement || document.mozFullScreenElement || document.msFullscreenElement);
  if (fullscreenBtn) {
    fullscreenBtn.title = isFullscreen ? '退出全屏 (Esc)' : '全屏 (F11)';
    // 更新按钮文本和样式
    if (isFullscreen) {
      fullscreenBtn.style.opacity = '0.8';
    } else {
      fullscreenBtn.style.opacity = '1';
    }
  }
}

// 监听全屏事件
document.addEventListener('fullscreenchange', handleFullscreenChange);
document.addEventListener('webkitfullscreenchange', handleFullscreenChange);
document.addEventListener('mozfullscreenchange', handleFullscreenChange);
document.addEventListener('MSFullscreenChange', handleFullscreenChange);

// 键盘快捷键：F11 或 Esc 退出全屏
document.addEventListener('keydown', (e) => {
  if (e.key === 'F11') {
    e.preventDefault();
    toggleFullscreen();
  }
});

function cleanup() {
  if (currentPC) {
    try { currentPC.close(); } catch (e) { console.error('关闭连接失败:', e); }
    currentPC = null;
  }
  if (releaseHandler) {
    window.removeEventListener('pagehide', releaseHandler);
    window.removeEventListener('beforeunload', releaseHandler);
    releaseHandler = null;
  }
  if (player && player.srcObject) {
    player.srcObject.getTracks().forEach(track => track.stop());
    player.srcObject = null;
  }
}

async function setupViewer(retryCount = 0) {
  cleanup();
  
  try {
    currentPC = new RTCPeerConnection({
      iceServers: [
        {
          urls: ["stun:8.134.173.118:3478"]
        },
        {
          urls: ["turn:8.134.173.118:3478"],
          username: "webrtc",
          credential: "wo1990shizhu"
        }
      ]
    });

    currentPC.ontrack = (e) => {
      const statusDiv = document.getElementById('connection-status');
      if (statusDiv) statusDiv.remove();
      if (e.streams && e.streams[0]) {
        player.srcObject = e.streams[0];
        player.play().catch(err => console.error('播放失败:', err));
      }
    };

    currentPC.onconnectionstatechange = () => {
      const state = currentPC.connectionState;
      console.log('连接状态:', state);
      if (state === 'connected') {
        const statusDiv = document.getElementById('connection-status');
        if (statusDiv) statusDiv.remove();
      } else if (state === 'failed' || state === 'closed') {
        cleanup();
        if (retryCount < 5) {
          setTimeout(() => setupViewer(retryCount + 1), 2000);
        }
      }
    };

    await fetch('/viewer/open', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ip })
    });
    
    const statusDiv = document.createElement('div');
    statusDiv.id = 'connection-status';
    statusDiv.style.cssText = 'position: fixed; top: 20px; right: 20px; background: rgba(0,0,0,0.8); color: white; padding: 10px 20px; border-radius: 5px; z-index: 1000;';
    statusDiv.textContent = '等待客户端连接...';
    document.body.appendChild(statusDiv);
    
    await new Promise(resolve => setTimeout(resolve, 5000));
    statusDiv.textContent = '正在建立连接...';

    currentPC.addTransceiver('video', { direction: 'recvonly' });
    const offer = await currentPC.createOffer();
    await currentPC.setLocalDescription(offer);

    const resp = await fetch('/view', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sdp: currentPC.localDescription.sdp, type: currentPC.localDescription.type, timeout: 0 })
    });

    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`HTTP ${resp.status}: ${text}`);
    }

    const answer = await resp.json();
    if (!answer.sdp) throw new Error("Server did not return SDP");
    await currentPC.setRemoteDescription(answer);

    // 监听视频控件的全屏事件（原生控件）
    player.addEventListener('webkitbeginfullscreen', () => {
      console.log('开始全屏（原生控件）');
    });
    player.addEventListener('webkitendfullscreen', () => {
      console.log('结束全屏（原生控件）');
    });
    player.addEventListener('fullscreenchange', () => {
      handleFullscreenChange();
    });
    player.addEventListener('webkitfullscreenchange', () => {
      handleFullscreenChange();
    });
    
    // 双击视频进入全屏
    player.addEventListener('dblclick', (e) => {
      // 如果双击的是视频控件区域，不触发全屏
      if (e.target === player || e.target.closest('video')) {
        toggleFullscreen();
      }
    });
    
    // 确保视频控件支持全屏
    if (player.webkitSupportsFullscreen !== undefined) {
      player.webkitSupportsFullscreen = true;
    }

    releaseHandler = () => {
      fetch('/viewer/close', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ip }),
        keepalive: true
      }).catch(() => {});
      cleanup();
    };
    window.addEventListener('pagehide', releaseHandler);
    window.addEventListener('beforeunload', releaseHandler);

  } catch (err) {
    console.error('viewer setup failed:', err);
    cleanup();
    if (retryCount < 5) {
      setTimeout(() => setupViewer(retryCount + 1), 2000);
    } else {
      alert('预览初始化失败，请刷新页面重试: ' + err.message);
    }
  }
}

setupViewer();
  </script>
</body>
</html>"""
    return web.Response(text=html, content_type='text/html')


def create_app() -> web.Application:
    """创建 aiohttp 应用"""
    app = web.Application()
    
    # 路由
    app.router.add_post('/offer', handle_offer)
    app.router.add_post('/view', handle_view)
    app.router.add_post('/viewer/open', handle_viewer_open)
    app.router.add_post('/viewer/close', handle_viewer_close)
    app.router.add_get('/preview', handle_preview)
    app.router.add_get('/status', handle_status)
    
    # CORS 支持
    @web.middleware
    async def cors_middleware(request: Request, handler):
        if request.method == 'OPTIONS':
            return web.Response(headers={
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type',
            })
        response = await handler(request)
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response
    
    app.middlewares.append(cors_middleware)
    
    return app


async def init_app() -> web.Application:
    """初始化应用"""
    return create_app()


def start_webrtc_server() -> None:
    """启动 WebRTC 服务器（供 backend.py 调用）"""
    main()


def main() -> None:
    """启动 WebRTC 服务器"""
    print(f"🚀 启动 WebRTC 服务器 (port {SERVER_PORT})")
    app = init_app()
    web.run_app(app, host="0.0.0.0", port=SERVER_PORT)


if __name__ == "__main__":
    main()
