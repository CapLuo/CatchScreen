"""
WebRTC 服务器 - 用于视频直播点看功能
"""
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

@app.route("/webrtc", methods=["POST"])
def webrtc():
    """WebRTC 连接处理"""
    data = request.json
    # 这里可以添加具体的 WebRTC 逻辑
    return jsonify({"status": "connected"})


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
    // 这里应当接入实际 WebRTC 逻辑，当前为占位演示
    const urlParams = new URLSearchParams(location.search);
    const ip = urlParams.get('ip') || '-';
    document.getElementById('ip').textContent = `(${ip})`;
    // TODO: 在此实现与客户端的 WebRTC 协商，设置 player.srcObject
  </script>
</body>
</html>
"""


@app.route('/preview')
def preview():
    return render_template_string(PREVIEW_HTML)

def start_webrtc_server():
    """启动 WebRTC 服务器"""
    print("🚀 启动 WebRTC 服务器 (port 8080)")
    app.run(host="0.0.0.0", port=8080, debug=False)

if __name__ == "__main__":
    start_webrtc_server()

