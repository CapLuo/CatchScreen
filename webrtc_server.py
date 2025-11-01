"""
WebRTC 服务器 - 用于视频直播点看功能
"""
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay
import asyncio
import threading
import requests
import time
from datetime import datetime

app = Flask(__name__)
CORS(app)

# 全局：发布者轨道中继与连接集合
relay = MediaRelay()
pcs = set()
pc_info = {}  # {pc_id: {"type": "publisher/viewer", "ip": "...", "created_at": timestamp, "remote_addr": "..."}}
published = {"video": None, "audio": None}
VIEWER_ACTIVE = False

# 全局常驻事件循环（在线程中运行），承载所有 aiortc 会话
_loop = asyncio.new_event_loop()

def _loop_runner(loop: asyncio.AbstractEventLoop):
    asyncio.set_event_loop(loop)
    loop.run_forever()

_loop_thread = threading.Thread(target=_loop_runner, args=(_loop,), daemon=True)
_loop_thread.start()


@app.errorhandler(Exception)
def handle_exception(err):
    """统一错误为 JSON，便于前端处理。仅针对 webrtc 相关路径。"""
    path = request.path or ""
    if path in ("/webrtc", "/view", "/viewer/open", "/viewer/close"):
        status = getattr(err, 'code', 500)
        return jsonify({"error": str(err)}), status
    # 其它路径保持默认（HTML）
    raise err

def _run_async(coro):
    """在线程常驻事件循环上同步执行协程，返回结果。"""
    fut = asyncio.run_coroutine_threadsafe(coro, _loop)
    return fut.result()


def _log_connection(level, pc_id, msg, **kwargs):
    """记录连接相关日志"""
    info = pc_info.get(pc_id, {})
    conn_type = info.get("type", "unknown")
    ip = info.get("ip", "N/A")
    remote = info.get("remote_addr", "N/A")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    extra = " ".join([f"{k}={v}" for k, v in kwargs.items()])
    print(f"[{timestamp}] [{level}] [WebRTC-{conn_type.upper()}] PC#{pc_id[:8]} IP={ip} Remote={remote} | {msg} {extra}".strip())


def _setup_pc_logging(pc, pc_id, conn_type, ip=None, remote_addr=None):
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
        
        if state == "closed":
            info = pc_info.pop(pc_id, {})
            duration = time.time() - info.get("created_at", time.time())
            _log_connection("INFO", pc_id, f"连接已关闭 | 持续时间: {duration:.2f}秒", 
                          duration=f"{duration:.2f}s")
    
    @pc.on("iceconnectionstatechange")
    def on_ice_state_change():
        state = pc.iceConnectionState
        _log_connection("INFO", pc_id, f"ICE 连接状态: {state}")
    
    @pc.on("signalingstatechange")
    def on_signaling_state_change():
        state = pc.signalingState
        _log_connection("DEBUG", pc_id, f"信令状态: {state}")


@app.route("/webrtc", methods=["POST"])
def webrtc_publish():
    """发布接口：客户端（屏幕抓取端）发送 Offer，服务器保存上行轨并返回 Answer"""
    payload = request.json or {}
    offer_sdp = payload.get("sdp")
    offer_type = payload.get("type", "offer")
    if not offer_sdp:
        return jsonify({"error": "missing sdp"}), 400

    pc = RTCPeerConnection()
    pc_id = id(pc)
    remote_addr = request.remote_addr
    
    # 从 SDP 或请求中提取 IP（可选）
    ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or remote_addr
    
    _setup_pc_logging(pc, str(pc_id), "publisher", ip=ip, remote_addr=remote_addr)
    pcs.add(pc)
    _log_connection("INFO", str(pc_id), "创建发布者连接")

    @pc.on("track")
    def on_track(track):
        _log_connection("INFO", str(pc_id), f"收到发布者媒体轨: kind={track.kind}")
        if track.kind == "video":
            published["video"] = relay.subscribe(track)
            _log_connection("INFO", str(pc_id), "视频轨已发布并订阅")
        elif track.kind == "audio":
            published["audio"] = relay.subscribe(track)
            _log_connection("INFO", str(pc_id), "音频轨已发布并订阅")

    async def handle():
        _log_connection("INFO", str(pc_id), "开始 SDP 协商")
        await pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type=offer_type))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        _log_connection("INFO", str(pc_id), "SDP 协商完成，返回 Answer")
        return pc.localDescription

    try:
        local_desc = _run_async(handle())
        return jsonify({"sdp": local_desc.sdp, "type": local_desc.type})
    except Exception as e:
        _log_connection("ERROR", str(pc_id), f"发布协商失败: {e}")
        raise


@app.route("/view", methods=["POST"])
def webrtc_view():
    """观看接口：观众端发送 Offer，服务器把已发布轨添加后返回 Answer"""
    payload = request.json or {}
    offer_sdp = payload.get("sdp")
    offer_type = payload.get("type", "offer")
    timeout_s = payload.get("timeout")
    if not offer_sdp:
        return jsonify({"error": "missing sdp"}), 400

    pc = RTCPeerConnection()
    pc_id = id(pc)
    remote_addr = request.remote_addr
    ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or remote_addr
    
    _setup_pc_logging(pc, str(pc_id), "viewer", ip=ip, remote_addr=remote_addr)
    pcs.add(pc)
    _log_connection("INFO", str(pc_id), "创建观众连接")

    async def handle():
        # 等待发布端上线（timeout<=0 表示无限等待）
        if not (published["video"] or published["audio"]):
            _log_connection("INFO", str(pc_id), "等待发布端上线...")
            loop = asyncio.get_running_loop()
            if timeout_s is None or float(timeout_s) <= 0:
                # 无限等待，直至有发布轨
                wait_start = time.time()
                while not (published["video"] or published["audio"]):
                    await asyncio.sleep(0.2)
                wait_duration = time.time() - wait_start
                _log_connection("INFO", str(pc_id), f"发布端已上线，等待耗时: {wait_duration:.2f}秒")
            else:
                deadline = loop.time() + float(timeout_s)
                wait_start = time.time()
                while not (published["video"] or published["audio"]) and loop.time() < deadline:
                    await asyncio.sleep(0.2)
                if not (published["video"] or published["audio"]):
                    wait_duration = time.time() - wait_start
                    _log_connection("WARN", str(pc_id), f"等待超时，无发布轨可用 | 等待时长: {wait_duration:.2f}秒")
                    return None
                wait_duration = time.time() - wait_start
                _log_connection("INFO", str(pc_id), f"发布端已上线，等待耗时: {wait_duration:.2f}秒")

        # 将已发布轨添加到观众连接
        tracks_added = []
        if published["video"]:
            pc.addTrack(published["video"])
            tracks_added.append("video")
        if published["audio"]:
            pc.addTrack(published["audio"])
            tracks_added.append("audio")
        _log_connection("INFO", str(pc_id), f"已添加媒体轨: {', '.join(tracks_added)}")

        _log_connection("INFO", str(pc_id), "开始 SDP 协商")
        await pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type=offer_type))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        _log_connection("INFO", str(pc_id), "SDP 协商完成，返回 Answer")
        return pc.localDescription

    try:
        local_desc = _run_async(handle())
        if local_desc is None:
            _log_connection("ERROR", str(pc_id), "协商失败: 无发布轨可用")
            return jsonify({"error": "no published tracks"}), 409
        return jsonify({"sdp": local_desc.sdp, "type": local_desc.type})
    except Exception as e:
        _log_connection("ERROR", str(pc_id), f"观看协商失败: {e}")
        raise


PREVIEW_HTML = """
<!doctype html>
<html lang=zh-CN>
<head>
  <meta charset=utf-8>
  <meta name=viewport content="width=device-width,initial-scale=1">
  <title>WebRTC 实时预览</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body { background:#0b0d10; color:#eaecef; }
    video { width:100%; max-height:80vh; border-radius: 10px; background:#111; }
  </style>
</head>
<body class="p-3 p-md-4">
  <div class="container">
    <h5 class="mb-3">实时预览 <small class="text-secondary" id="ip"></small></h5>
    <div class="card bg-dark border-0 shadow-sm">
      <div class="card-body">
        <video id="player" autoplay playsinline controls></video>
      </div>
    </div>
  </div>

<script>
const player = document.getElementById('player');
const urlParams = new URLSearchParams(location.search);
const ip = urlParams.get('ip') || '-';

// 每次尝试建立连接
async function setupViewer(retryCount = 0) {
  try {
    const pc = new RTCPeerConnection({
      iceServers: [{ urls: ['stun:stun.l.google.com:19302'] }]
    });

    pc.ontrack = (e) => { player.srcObject = e.streams[0]; };

    // 通知服务端：观众进入
    await fetch('/viewer/open', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ip })
    });

    // 明确声明接收端媒体
    pc.addTransceiver('video', { direction: 'recvonly' });

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    const resp = await fetch('/view', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sdp: pc.localDescription.sdp, type: pc.localDescription.type, timeout: 0 })
    });

    const answer = await resp.json();
    if (!answer.sdp) throw new Error("Server did not return SDP");

    await pc.setRemoteDescription(answer);

    // 离开页面时关闭连接
    const release = () => {
      fetch('/viewer/close', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ip }),
        keepalive: true
      }).catch(() => {});
      if (pc) pc.close();
    };
    window.addEventListener('pagehide', release);
    window.addEventListener('beforeunload', release);

  } catch (err) {
    console.error('viewer setup failed:', err);
    if (retryCount < 5) {
      // 自动重试
      setTimeout(() => setupViewer(retryCount + 1), 2000);
    } else {
      alert('预览初始化失败，请刷新页面重试');
    }
  }
}

// 启动
setupViewer();
</script>
</body>
</html>
"""


@app.route('/preview')
def preview():
    return render_template_string(PREVIEW_HTML)

@app.route('/viewer/open', methods=['POST'])
def viewer_open():
    global VIEWER_ACTIVE
    VIEWER_ACTIVE = True
    # 从请求中获取 IP，更新后端 webrtc_direct=1
    payload = request.json or {}
    ip = payload.get("ip")
    if ip and ip != '-':
        try:
            requests.patch(
                f"http://127.0.0.1:5000/api/folders/{ip}/webrtc_direct",
                json={"webrtc_direct": True},
                timeout=2
            )
        except Exception as e:
            print(f"[viewer/open] 更新 webrtc_direct 失败: {e}")
    return jsonify({"viewer": True})


@app.route('/viewer/close', methods=['POST'])
def viewer_close():
    global VIEWER_ACTIVE, published
    payload = request.json or {}
    ip = payload.get("ip", "N/A")
    remote_addr = request.remote_addr
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [INFO] [VIEWER_CLOSE] IP={ip} Remote={remote_addr} | 开始关闭所有连接")
    
    VIEWER_ACTIVE = False
    
    # 从请求中获取 IP，更新后端 webrtc_direct=0
    if ip and ip != '-' and ip != 'N/A':
        try:
            requests.patch(
                f"http://127.0.0.1:5000/api/folders/{ip}/webrtc_direct",
                json={"webrtc_direct": False},
                timeout=2
            )
            print(f"[{timestamp}] [INFO] [VIEWER_CLOSE] IP={ip} | webrtc_direct 已更新为 0")
        except Exception as e:
            print(f"[{timestamp}] [ERROR] [VIEWER_CLOSE] IP={ip} | 更新 webrtc_direct 失败: {e}")
    
    # 关闭所有连接并清空发布轨
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
            _log_connection("INFO", pc_id, f"主动关闭连接 | 当前状态: {current_state}")
            _run_async(pc.close())
            closed_count += 1
        except Exception as e:
            _log_connection("ERROR", pc_id, f"关闭连接时出错: {e}")
        finally:
            pcs.discard(pc)
            pc_info.pop(pc_id, None)
    
    # 记录发布轨状态
    had_video = published["video"] is not None
    had_audio = published["audio"] is not None
    
    published = {"video": None, "audio": None}
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [INFO] [VIEWER_CLOSE] IP={ip} | 关闭完成:")
    print(f"  - 关闭连接数: {closed_count} (发布者: {publisher_count}, 观众: {viewer_count})")
    print(f"  - 发布轨清理: video={'已清空' if had_video else '无'}, audio={'已清空' if had_audio else '无'}")
    print(f"  - 剩余连接数: {len(pcs)}")
    
    return jsonify({"viewer": False, "closed": closed_count})

def start_webrtc_server():
    """启动 WebRTC 服务器"""
    print("🚀 启动 WebRTC 服务器 (port 8080)")
    app.run(host="0.0.0.0", port=8080, debug=False)

if __name__ == "__main__":
    start_webrtc_server()

