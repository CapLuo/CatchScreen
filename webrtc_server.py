"""
WebRTC 服务器 - 用于视频直播点看功能
"""
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay
import asyncio
import threading

app = Flask(__name__)
CORS(app)

# 全局：发布者轨道中继与连接集合
relay = MediaRelay()
pcs = set()
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


@app.route("/webrtc", methods=["POST"])
def webrtc_publish():
    """发布接口：客户端（屏幕抓取端）发送 Offer，服务器保存上行轨并返回 Answer"""
    payload = request.json or {}
    offer_sdp = payload.get("sdp")
    offer_type = payload.get("type", "offer")
    timeout_s = payload.get("timeout")
    if not offer_sdp:
        return jsonify({"error": "missing sdp"}), 400

    pc = RTCPeerConnection()
    pcs.add(pc)

    @pc.on("track")
    def on_track(track):
        if track.kind == "video":
            published["video"] = relay.subscribe(track)
        elif track.kind == "audio":
            published["audio"] = relay.subscribe(track)

    async def handle():
        await pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type=offer_type))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        return pc.localDescription

    local_desc = _run_async(handle())
    return jsonify({"sdp": local_desc.sdp, "type": local_desc.type})


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
    pcs.add(pc)

    async def handle():
        # 等待发布端上线（timeout<=0 表示无限等待）
        if not (published["video"] or published["audio"]):
            loop = asyncio.get_running_loop()
            if timeout_s is None or float(timeout_s) <= 0:
                # 无限等待，直至有发布轨
                while not (published["video"] or published["audio"]):
                    await asyncio.sleep(0.2)
            else:
                deadline = loop.time() + float(timeout_s)
                while not (published["video"] or published["audio"]) and loop.time() < deadline:
                    await asyncio.sleep(0.2)
                if not (published["video"] or published["audio"]):
                    return None

        # 将已发布轨添加到观众连接
        if published["video"]:
            pc.addTrack(published["video"])
        if published["audio"]:
            pc.addTrack(published["audio"])

        await pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type=offer_type))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        return pc.localDescription

    local_desc = _run_async(handle())
    if local_desc is None:
        return jsonify({"error": "no published tracks"}), 409
    return jsonify({"sdp": local_desc.sdp, "type": local_desc.type})


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
    const urlParams = new URLSearchParams(location.search);
    const ip = urlParams.get('ip') || '-';
    document.getElementById('ip').textContent = `(${ip})`;

    const player = document.getElementById('player');
    const pc = new RTCPeerConnection({
      iceServers: [{ urls: ['stun:stun.l.google.com:19302'] }]
    });
    pc.ontrack = (e) => { player.srcObject = e.streams[0]; };

    (async () => {
      // 通知服务端：观众进入
      try { await fetch('/viewer/open', { method: 'POST' }); } catch {}
      try {
        // 明确声明接收端媒体，确保 Offer 包含 video m-line
        pc.addTransceiver('video', { direction: 'recvonly' });
        // 如需音频可以打开：
        // pc.addTransceiver('audio', { direction: 'recvonly' });
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);
        const resp = await fetch('/view', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sdp: pc.localDescription.sdp, type: pc.localDescription.type, timeout: 0 })
        });
        const ct = resp.headers.get('content-type') || '';
        const text = await resp.text();
        if (!resp.ok) {
          throw new Error(`HTTP ${resp.status}: ${text}`);
        }
        if (!ct.includes('application/json')) {
          throw new Error(text || 'Server did not return JSON');
        }
        const answer = JSON.parse(text);
        if (!answer.sdp) throw new Error(JSON.stringify(answer));
        await pc.setRemoteDescription(answer);
      } catch (err) {
        console.error('viewer setup failed:', err);
        alert('预览初始化失败：' + err.message);
      }
    })();

    // 观众离开时通知服务端关闭连接
    const release = () => { try { navigator.sendBeacon('/viewer/close'); } catch { fetch('/viewer/close', { method: 'POST', keepalive: true }); } };
    window.addEventListener('pagehide', release);
    window.addEventListener('beforeunload', release);
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
    return jsonify({"viewer": True})


@app.route('/viewer/close', methods=['POST'])
def viewer_close():
    global VIEWER_ACTIVE, published
    VIEWER_ACTIVE = False
    for pc in list(pcs):
        try:
            _run_async(pc.close())
        except Exception:
            pass
        finally:
            pcs.discard(pc)
    published = {"video": None, "audio": None}
    return jsonify({"viewer": False})

def start_webrtc_server():
    """启动 WebRTC 服务器"""
    print("🚀 启动 WebRTC 服务器 (port 8080)")
    app.run(host="0.0.0.0", port=8080, debug=False)

if __name__ == "__main__":
    start_webrtc_server()

