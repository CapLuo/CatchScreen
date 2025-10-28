"""
Flask 版视频上传管理端（带登录系统）
----------------------------------
功能：
✅ 管理员登录 / 注销
✅ 按 IP 自动建文件夹存放视频
✅ 修改备注名
✅ 删除文件夹
✅ 浏览器查看、在线播放
✅ 直播点看

依赖安装：
    pip install flask werkzeug

运行：
    python flask_video_admin.py
访问：
    http://127.0.0.1:5000/
"""

import os
import shutil
from datetime import datetime
from flask import (
    Flask, request, render_template_string, jsonify,
    send_from_directory, session, redirect, url_for
)
from multiprocessing import Process
from webrtc_server import start_webrtc_server

# ------------- 基础配置 -------------
app = Flask(__name__)
app.secret_key = "super_secret_key_123"  # session 加密密钥
UPLOAD_ROOT = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_ROOT, exist_ok=True)

# 模拟数据库（简单字典，可换 SQLite）
db = {}  # {"ip": {"remark": "..."}}

# 管理员账号（可改）
ADMIN_USER = "admin"
ADMIN_PASS = "123456"


# ------------- HTML 模板 -------------
LOGIN_HTML = """
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8">
    <title>登录 - 视频管理后台</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
  </head>
  <body class="bg-light d-flex align-items-center" style="height:100vh;">
    <div class="container text-center">
      <div class="card shadow-lg mx-auto" style="max-width:400px;">
        <div class="card-body">
          <h4 class="card-title mb-4">🎬 视频管理后台登录</h4>
          <form method="post" action="/login">
            <div class="mb-3">
              <input class="form-control" name="username" placeholder="用户名" required>
            </div>
            <div class="mb-3">
              <input type="password" class="form-control" name="password" placeholder="密码" required>
            </div>
            <button type="submit" class="btn btn-primary w-100">登录</button>
          </form>
          {% if error %}
          <div class="alert alert-danger mt-3">{{ error }}</div>
          {% endif %}
        </div>
      </div>
    </div>
  </body>
</html>
"""

# ------------------------------------
# 模板
# ------------------------------------
INDEX_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>📂 视频管理端</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
<div class="container py-4">
  <h3 class="mb-4 text-center">📁 视频文件夹列表</h3>
  <div class="row">
    {% for ip in folders %}
    <div class="col-12 col-md-4 col-lg-3 mb-3">
      <div class="card shadow-sm p-3">
        <h6>{{ ip }}</h6>
        <a href="/folder/{{ ip }}" class="btn btn-sm btn-primary mt-2">查看视频</a>
      </div>
    </div>
    {% else %}
    <p class="text-muted text-center">暂无文件夹</p>
    {% endfor %}
  </div>
</div>
</body>
</html>
"""

FOLDER_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>{{ ip }} - 视频列表</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
</head>
<body class="bg-light">
<div class="container py-4">
  <a href="/" class="btn btn-outline-secondary mb-3">← 返回文件夹列表</a>
  <h4>📹 {{ ip }} 的视频（{{ videos|length }} 个）</h4>

  <div class="row mt-3">
    {% for v in videos %}
    <div class="col-12 col-md-4 col-lg-3 mb-3">
      <div class="card shadow-sm" onclick="showVideo('{{ ip }}','{{ v }}')">
        <video src="/uploads/{{ ip }}/{{ v }}" muted preload="metadata" style="width:100%;border-radius:8px;"></video>
        <div class="card-body py-2">
          <small>{{ v }}</small>
        </div>
      </div>
    </div>
    {% else %}
    <p class="text-muted text-center">暂无视频</p>
    {% endfor %}
  </div>
</div>

<!-- 播放弹窗 -->
<div class="modal fade" id="videoModal" tabindex="-1">
  <div class="modal-dialog modal-dialog-centered modal-lg">
    <div class="modal-content">
      <div class="modal-body">
        <video id="player" controls autoplay style="width:100%;border-radius:10px;"></video>
      </div>
    </div>
  </div>
</div>

<script>
function showVideo(ip, filename){
  const modal = new bootstrap.Modal(document.getElementById('videoModal'));
  const player = document.getElementById('player');
  player.src = `/uploads/${ip}/${filename}`;
  modal.show();
}
</script>
</body>
</html>
"""


# ---------------- 工具函数 ----------------
def folder_path(ip: str):
    """根据 IP 返回对应上传文件夹"""
    safe_ip = ip.replace("/", "_")
    path = os.path.join(UPLOAD_ROOT, safe_ip)
    os.makedirs(path, exist_ok=True)
    return path


def login_required(func):
    """简单的登录保护装饰器"""
    from functools import wraps
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return func(*args, **kwargs)
    return wrapper


# ---------------- 登录相关 ----------------
@app.route("/login", methods=["GET", "POST"])
def login():
    """登录页面"""
    if request.method == "POST":
        user = request.form.get("username")
        pw = request.form.get("password")
        if user == ADMIN_USER and pw == ADMIN_PASS:
            session["logged_in"] = True
            return redirect("/")
        return render_template_string(LOGIN_HTML, error="账号或密码错误")
    return render_template_string(LOGIN_HTML)


@app.route("/logout")
def logout():
    """退出登录"""
    session.clear()
    return redirect("/login")


# ---------------- 主页面与接口 ----------------
@app.route("/")
@login_required
def index():
    folders = sorted(os.listdir(UPLOAD_ROOT))
    return render_template_string(INDEX_HTML, folders=folders)

# ------------------------------------
# 静态视频访问
# ------------------------------------
@app.route("/uploads/<ip>/<filename>")
@login_required
def serve_video(ip, filename):
    path = folder_path(ip)
    print(path)
    return send_from_directory(path, filename, mimetype="video/mp4")


@app.route("/list", methods=["GET"])
def list_folders():
    """返回文件夹和视频列表"""
    data = []
    for folder_name in os.listdir(UPLOAD_ROOT):
        folder_path_ = os.path.join(UPLOAD_ROOT, folder_name)
        if os.path.isdir(folder_path_):
            videos = [
                v for v in os.listdir(folder_path_)
                if v.lower().endswith((".mp4", ".avi", ".mov"))
            ]
            data.append({"ip": folder_name, "videos": videos})
    return jsonify(data)


# ------------------------------------
# 文件夹详情页：展示视频
# ------------------------------------
@app.route("/folder/<ip>")
def folder_detail(ip):
    path = folder_path(ip)
    videos = [
        v for v in os.listdir(path)
        if v.lower().endswith((".mp4", ".avi", ".mov"))
    ]
    # 按修改时间倒序排序
    videos = sorted(videos, key=lambda v: os.path.getmtime(os.path.join(path, v)), reverse=True)
    return render_template_string(FOLDER_HTML, ip=ip, videos=videos)

@app.route("/api/folders", methods=["POST"])
@login_required
def create_folder():
    """创建文件夹"""
    data = request.json
    ip = data.get("ip")
    remark = data.get("remark", "")
    if not ip:
        return jsonify({"error": "ip required"}), 400
    db[ip] = {"remark": remark}
    folder_path(ip)
    return jsonify({"msg": "created"})


@app.route("/api/folders/<ip>/remark", methods=["PATCH"])
@login_required
def update_remark(ip):
    """修改备注"""
    data = request.json
    if ip not in db:
        return jsonify({"error": "not found"}), 404
    db[ip]["remark"] = data.get("remark", "")
    return jsonify({"msg": "ok"})


@app.route("/api/folders/<ip>", methods=["DELETE"])
@login_required
def delete_folder(ip):
    """删除文件夹"""
    if ip in db:
        db.pop(ip)
    path = folder_path(ip)
    if os.path.exists(path):
        shutil.rmtree(path)
    return jsonify({"msg": "deleted"})

@app.route("/upload/<ip>", methods=["POST"])
def upload_video(ip):
    """上传视频（不需登录，文件名为时间）"""
    folder = folder_path(ip)
    if "file" not in request.files:
        return jsonify({"error": "no file"}), 400
    f = request.files["file"]

    # 用时间生成文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = os.path.splitext(f.filename)[1] or ".mp4"
    filename = f"{timestamp}{ext}"

    save_path = os.path.join(folder, filename)
    f.save(save_path)

    print(f"[UPLOAD] {request.remote_addr} 上传 {filename} -> {folder}")
    return jsonify({"filename": filename})



# ---------------- 启动 ----------------
if __name__ == "__main__":
    
    # 启动 WebRTC 子进程
    p = Process(target=start_webrtc_server, daemon=True)
    p.start()
    
    print("✅ WebRTC 服务已启动 (port 8080)")
    app.run(host="0.0.0.0", port=5000, debug=True)
