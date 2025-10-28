"""
Flask 后端 API - 视频上传管理系统
----------------------------------
提供 REST API 接口，支持前后端分离
"""

import os
import shutil
import sqlite3
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, session, send_file, g
from flask_cors import CORS
from multiprocessing import Process
from webrtc_server import start_webrtc_server
from functools import wraps
from db_manage import init_db as init_db_tool

# ------------- 基础配置 -------------
app = Flask(__name__)
app.secret_key = "super_secret_key_123"  # session 加密密钥
CORS(app, supports_credentials=True)  # 允许跨域请求

UPLOAD_ROOT = os.path.join(os.path.dirname(__file__), "uploads")
FRONTEND_ROOT = os.path.join(os.path.dirname(__file__), "frontend")
DB_PATH = os.path.join(os.path.dirname(__file__), "database.db")
os.makedirs(UPLOAD_ROOT, exist_ok=True)

# 管理员账号（可改）
ADMIN_USER = "admin"
ADMIN_PASS = "123456"


# ---------------- 数据库管理 ----------------
def get_db():
    """获取数据库连接"""
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(e=None):
    """关闭数据库连接"""
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    """初始化数据库表（委托 db_manage）"""
    init_db_tool()


# 注册关闭回调
app.teardown_appcontext(close_db)

# ---------------- 工具函数 ----------------
def folder_path(ip: str):
    """根据 IP 返回对应上传文件夹"""
    safe_ip = ip.replace("/", "_")
    path = os.path.join(UPLOAD_ROOT, safe_ip)
    os.makedirs(path, exist_ok=True)
    return path


def login_required(func):
    """简单的登录保护装饰器"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return jsonify({"error": "未登录"}), 401
        return func(*args, **kwargs)
    return wrapper


# ---------------- 登录相关 API ----------------
@app.route("/api/login", methods=["POST"])
def login():
    """登录接口"""
    data = request.json
    username = data.get("username")
    password = data.get("password")
    
    if username == ADMIN_USER and password == ADMIN_PASS:
        session["logged_in"] = True
        return jsonify({"success": True, "msg": "登录成功"})
    return jsonify({"success": False, "error": "账号或密码错误"}), 401


@app.route("/api/logout", methods=["POST"])
def logout():
    """退出登录接口"""
    session.clear()
    return jsonify({"success": True, "msg": "已退出"})


@app.route("/api/check_login", methods=["GET"])
def check_login():
    """检查登录状态"""
    is_logged_in = session.get("logged_in", False)
    return jsonify({"logged_in": is_logged_in})


# ---------------- 文件夹管理 API ----------------
@app.route("/api/folders", methods=["GET"])
@login_required
def list_folders():
    """获取所有文件夹列表"""
    db = get_db()
    folders = []
    
    for folder_name in sorted(os.listdir(UPLOAD_ROOT)):
        folder_path_ = os.path.join(UPLOAD_ROOT, folder_name)
        if os.path.isdir(folder_path_):
            videos = [
                v for v in os.listdir(folder_path_)
                if v.lower().endswith((".mp4", ".avi", ".mov", ".webm", ".mkv"))
            ]
            
            # 从数据库获取备注
            cursor = db.execute('SELECT remark FROM folders WHERE ip = ?', (folder_name,))
            row = cursor.fetchone()
            remark = row['remark'] if row else ""

            # 最近上传时间与在线状态（40分钟内有上传视为在线）
            last_row = db.execute(
                'SELECT uploaded_at FROM videos WHERE ip = ? ORDER BY uploaded_at DESC LIMIT 1',
                (folder_name,)
            ).fetchone()
            last_upload_at = last_row['uploaded_at'] if last_row else None

            online = False
            if last_upload_at:
                try:
                    last_dt = datetime.strptime(last_upload_at, "%Y-%m-%d %H:%M:%S")
                    online = (datetime.utcnow() - last_dt).total_seconds() <= 40 * 60
                except Exception:
                    online = False
            
            folders.append({
                "ip": folder_name,
                "video_count": len(videos),
                "remark": remark,
                "last_upload_at": last_upload_at,
                "online": online
            })
    
    return jsonify({"folders": folders})


@app.route("/api/folders", methods=["POST"])
@login_required
def create_folder():
    """创建文件夹"""
    data = request.json
    ip = data.get("ip")
    remark = data.get("remark", "")
    if not ip:
        return jsonify({"error": "ip required"}), 400
    
    db = get_db()
    try:
        # 插入数据库
        db.execute(
            'INSERT INTO folders (ip, remark) VALUES (?, ?)',
            (ip, remark)
        )
        db.commit()
    except sqlite3.IntegrityError:
        # 如果已存在，则更新
        db.execute(
            'UPDATE folders SET remark = ? WHERE ip = ?',
            (remark, ip)
        )
        db.commit()
    
    folder_path(ip)
    return jsonify({"msg": "created"})


@app.route("/api/folders/<ip>", methods=["GET"])
@login_required
def get_folder_detail(ip):
    """获取文件夹详情和视频列表"""
    path = folder_path(ip)
    if not os.path.exists(path):
        return jsonify({"error": "文件夹不存在"}), 404
    
    videos = [
        v for v in os.listdir(path)
        if v.lower().endswith((".mp4", ".avi", ".mov", ".webm", ".mkv"))
    ]
    # 按修改时间倒序排序
    videos = sorted(videos, key=lambda v: os.path.getmtime(os.path.join(path, v)), reverse=True)
    
    # 从数据库获取备注、最近上传与在线状态
    db = get_db()
    cursor = db.execute('SELECT remark FROM folders WHERE ip = ?', (ip,))
    row = cursor.fetchone()
    remark = row['remark'] if row else ""

    last_row = db.execute(
        'SELECT uploaded_at FROM videos WHERE ip = ? ORDER BY uploaded_at DESC LIMIT 1',
        (ip,)
    ).fetchone()
    last_upload_at = last_row['uploaded_at'] if last_row else None

    online = False
    if last_upload_at:
        try:
            last_dt = datetime.strptime(last_upload_at, "%Y-%m-%d %H:%M:%S")
            online = (datetime.utcnow() - last_dt).total_seconds() <= 40 * 60
        except Exception:
            online = False
    
    return jsonify({
        "ip": ip,
        "remark": remark,
        "videos": videos,
        "last_upload_at": last_upload_at,
        "online": online
    })


@app.route("/api/folders/<ip>/remark", methods=["PATCH"])
@login_required
def update_remark(ip):
    """修改备注"""
    data = request.json
    db = get_db()
    
    # 检查是否存在
    cursor = db.execute('SELECT ip FROM folders WHERE ip = ?', (ip,))
    if not cursor.fetchone():
        return jsonify({"error": "not found"}), 404
    
    # 更新备注
    db.execute(
        'UPDATE folders SET remark = ?, updated_at = CURRENT_TIMESTAMP WHERE ip = ?',
        (data.get("remark", ""), ip)
    )
    db.commit()
    
    return jsonify({"msg": "ok"})


@app.route("/api/folders/<ip>", methods=["DELETE"])
@login_required
def delete_folder(ip):
    """删除文件夹"""
    db = get_db()
    
    # 从数据库删除
    db.execute('DELETE FROM folders WHERE ip = ?', (ip,))
    db.commit()
    
    # 删除物理文件夹
    path = folder_path(ip)
    if os.path.exists(path):
        shutil.rmtree(path)
    
    return jsonify({"msg": "deleted"})


# ---------------- 视频管理 API ----------------
@app.route("/uploads/<ip>/<filename>")
@login_required
def serve_video(ip, filename):
    """提供视频文件访问"""
    path = folder_path(ip)
    return send_from_directory(path, filename, mimetype="video/mp4")


@app.route("/api/upload/<ip>", methods=["POST"])
def upload_video(ip):
    """上传视频（不需登录，文件名为时间）"""
    folder = folder_path(ip)
    if "file" not in request.files:
        return jsonify({"error": "no file"}), 400
    
    f = request.files["file"]
    if f.filename == "":
        return jsonify({"error": "no file"}), 400

    # 用时间生成文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = os.path.splitext(f.filename)[1] or ".mp4"
    filename = f"{timestamp}{ext}"

    save_path = os.path.join(folder, filename)
    f.save(save_path)
    
    # 记录到数据库
    db = get_db()
    try:
        # 确保文件夹记录存在
        cursor = db.execute('SELECT ip FROM folders WHERE ip = ?', (ip,))
        if not cursor.fetchone():
            db.execute('INSERT INTO folders (ip) VALUES (?)', (ip,))
        
        # 记录视频文件
        file_size = os.path.getsize(save_path)
        db.execute(
            'INSERT INTO videos (ip, filename, file_size) VALUES (?, ?, ?)',
            (ip, filename, file_size)
        )
        # 更新 folders.updated_at
        db.execute('UPDATE folders SET updated_at = CURRENT_TIMESTAMP WHERE ip = ?', (ip,))
        db.commit()
    except Exception as e:
        print(f"数据库记录失败: {e}")

    print(f"[UPLOAD] {request.remote_addr} 上传 {filename} -> {folder}")
    return jsonify({"filename": filename, "ip": ip})


# ---------------- 静态文件服务 ----------------
@app.route("/frontend/<path:filename>")
def serve_frontend(filename):
    """提供前端静态文件"""
    try:
        return send_from_directory(FRONTEND_ROOT, filename)
    except:
        return send_file(os.path.join(FRONTEND_ROOT, "login.html"))

@app.route("/frontend/")
def frontend_index():
    """前端主页重定向到登录页"""
    return send_file(os.path.join(FRONTEND_ROOT, "login.html"))


# ---------------- 启动 ----------------
if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    # 初始化数据库
    init_db()
    
    # 启动 WebRTC 子进程
    p = Process(target=start_webrtc_server, daemon=True)
    p.start()
    
    print("✅ WebRTC 服务已启动 (port 8080)")
    print("✅ 数据库初始化完成")
    print("✅ 后端 API 服务启动 (port 5000)")
    print("📝 访问: http://127.0.0.1:5000/frontend/login.html")
    # 重要：Windows 下禁用 reloader，避免重复启动子进程导致套接字异常
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)

