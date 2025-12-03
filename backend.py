"""
Flask 后端 API - 视频上传管理系统
----------------------------------
提供 REST API 接口，支持前后端分离
"""

import os
import shutil
import sqlite3
import sys
import logging
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, session, send_file, g
from flask_cors import CORS
from multiprocessing import Process
from preview_server import start_preview_server
from functools import wraps
from db_manage import init_db as init_db_tool

# ------------- 日志配置 -------------
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")

class DualWriter:
    """同时写入 原始stdout 和 日志文件"""
    def __init__(self, original_stream, file_handler):
        self.original_stream = original_stream
        self.file_handler = file_handler
        self.logger = logging.getLogger("print_logger_backend")
        self.logger.addHandler(file_handler)
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

    def write(self, message):
        self.original_stream.write(message)
        self.original_stream.flush()
        if message.strip():
            record = logging.LogRecord(
                name="print", level=logging.INFO, pathname="", lineno=0,
                msg=message.strip(), args=(), exc_info=None
            )
            self.file_handler.emit(record)

    def flush(self):
        self.original_stream.flush()

def setup_logging():
    """配置日志：按天轮转，永久保留"""
    os.makedirs(LOG_DIR, exist_ok=True)
    
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    
    # backend.log
    log_file = os.path.join(LOG_DIR, "backend.log")
    file_handler = TimedRotatingFileHandler(
        log_file, when='midnight', interval=1, backupCount=0, encoding='utf-8'
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    sys.stdout = DualWriter(sys.stdout, file_handler)
    sys.stderr = DualWriter(sys.stderr, file_handler)

# ------------- 基础配置 -------------
app = Flask(__name__)
app.secret_key = "super_secret_key_123"  # session 加密密钥
CORS(app, supports_credentials=True)  # 允许跨域请求

UPLOAD_ROOT = os.path.join(os.path.dirname(__file__), "uploads")
FRONTEND_ROOT = os.path.join(os.path.dirname(__file__), "frontend")
DATA_ROOT = os.path.join(os.path.dirname(__file__), "data")
DB_PATH = os.path.join(DATA_ROOT, "database.db")
# HLS_ROOT 必须与 webrtc_server.py 保持一致，且指向 Nginx 配置的真实路径
HLS_ROOT = os.environ.get('HLS_ROOT', "/tmp/hls")
os.makedirs(UPLOAD_ROOT, exist_ok=True)
os.makedirs(DATA_ROOT, exist_ok=True)
os.makedirs(HLS_ROOT, exist_ok=True)

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

@app.route("/.well-known/appspecific/com.chrome.devtools.json")
def devtools_probe():
    return ("", 204)  # 或返回 {} / 你自定义的配置

@app.route("/api/config", methods=["GET"])
def get_config():
    """获取服务器配置信息（用于前端动态配置）
    自动从请求中获取主机信息，构建正确的URL（包含端口）
    """
    # 从请求头获取协议（支持反向代理）
    protocol = request.headers.get('X-Forwarded-Proto', '') or request.scheme or 'http'
    # 确保协议格式正确
    if protocol and not protocol.startswith('http'):
        protocol = 'https' if protocol == 'https' or request.is_secure else 'http'
    if not protocol:
        protocol = 'https' if request.is_secure else 'http'
    
    # 从请求头获取主机（支持反向代理）
    host = request.headers.get('X-Forwarded-Host') or request.headers.get('Host') or request.host
    
    # 解析主机名和端口
    if ':' in host:
        hostname, current_port = host.rsplit(':', 1)
    else:
        hostname = host
        # 如果没有端口，根据协议推断标准端口
        current_port = '443' if protocol == 'https' else '80'
    
    # 从环境变量获取端口配置（支持自定义端口）
    api_port = os.environ.get('API_PORT', '5001')
    preview_port = os.environ.get('PREVIEW_PORT', '5002')
    
    # 构建URL的函数
    def build_url(port):
        """构建包含端口的完整URL
        对于非标准端口(非80/443)，始终包含端口号
        对于标准端口，根据实际情况决定是否包含端口
        """
        port_str = str(port)
        # 非标准端口，必须包含端口号
        if port_str not in ('80', '443'):
            return f"{protocol}://{hostname}:{port_str}"
        # 标准端口：如果当前请求使用了标准端口，可以不包含端口
        # 但如果当前请求使用了非标准端口，标准端口也应该包含端口（避免混淆）
        if current_port in ('80', '443'):
            # 当前使用标准端口，目标也是标准端口，可以不包含端口
            if port_str == '80' and protocol == 'http':
                return f"http://{hostname}"
            elif port_str == '443' and protocol == 'https':
                return f"https://{hostname}"
        # 其他情况，包含端口（更明确）
        return f"{protocol}://{hostname}:{port_str}"
    
    # 构建配置
    api_base = f"{build_url(api_port)}/api"
    preview_base = build_url(preview_port)
    uploads_base = f"{build_url(api_port)}/uploads"
    
    return jsonify({
        "apiBase": api_base,
        "previewBase": preview_base, # New name
        "webrtcBase": preview_base,  # Compatibility
        "uploadsBase": uploads_base,
        "hostname": hostname,
        "apiPort": api_port,
        "previewPort": preview_port, # New name
        "webrtcPort": preview_port   # Compatibility
    })

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
    """获取所有文件夹列表（以数据库 folders 为准，再补充文件与状态信息）
    支持搜索参数：?q=关键词（搜索 IP 或备注）
    """
    db = get_db()
    folders = []

    # 获取搜索关键词
    search_query = request.args.get("q", "").strip()
    
    # 1) 从数据库读取 folders 记录，支持搜索
    if search_query:
        # 使用 LIKE 进行模糊搜索，支持 IP 和备注
        search_pattern = f"%{search_query}%"
        rows = db.execute(
            'SELECT ip, device_id, remark, updated_at, upload_enabled, webrtc_direct FROM folders WHERE ip LIKE ? OR remark LIKE ? ORDER BY ip',
            (search_pattern, search_pattern)
        ).fetchall()
    else:
        rows = db.execute('SELECT ip, device_id, remark, updated_at, upload_enabled, webrtc_direct FROM folders ORDER BY ip').fetchall()

    # 2) 逐条补充视频与在线信息
    for row in rows:
        ip = row['ip']
        device_id = row['device_id']
        remark = row['remark'] or ""
        updated_at = row['updated_at']
        upload_enabled = int(row['upload_enabled']) if row['upload_enabled'] is not None else 1
        webrtc_direct = int(row['webrtc_direct']) if row['webrtc_direct'] is not None else 0

        # 确保物理目录存在
        path = folder_path(ip)
        # 统计视频数量
        video_count = 0
        if os.path.isdir(path):
            try:
                video_count = len([
                    v for v in os.listdir(path)
                    if v.lower().endswith((".mp4", ".avi", ".mov", ".webm", ".mkv"))
                ])
            except Exception:
                video_count = 0

        # 最近上传时间
        last_row = db.execute(
            'SELECT uploaded_at FROM videos WHERE ip = ? ORDER BY uploaded_at DESC LIMIT 1',
            (ip,)
        ).fetchone()
        last_upload_at = last_row['uploaded_at'] if last_row else None

        # 在线状态：基于 folders.updated_at（5 分钟内在线）
        online = False
        if updated_at:
            try:
                upd_dt = datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S")
                online = (datetime.utcnow() - upd_dt).total_seconds() <= 70
            except Exception:
                online = False

        folders.append({
            "ip": ip,
            "device_id": device_id,
            "video_count": video_count,
            "remark": remark,
            "last_upload_at": last_upload_at,
            "online": online,
            "upload_enabled": bool(upload_enabled),
            "webrtc_direct": bool(webrtc_direct)
        })

    return jsonify({"folders": folders})


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
    
    # 从数据库获取备注、配置、最近上传与在线状态
    db = get_db()
    cursor = db.execute('SELECT device_id, remark, upload_enabled, webrtc_direct FROM folders WHERE ip = ?', (ip,))
    row = cursor.fetchone()
    remark = row['remark'] if row else ""
    device_id = row['device_id'] if row else None
    upload_enabled = int(row['upload_enabled']) if row and row['upload_enabled'] is not None else 1
    webrtc_direct = int(row['webrtc_direct']) if row and row['webrtc_direct'] is not None else 0

    last_row = db.execute(
        'SELECT uploaded_at FROM videos WHERE ip = ? ORDER BY uploaded_at DESC LIMIT 1',
        (ip,)
    ).fetchone()
    last_upload_at = last_row['uploaded_at'] if last_row else None

    # 在线状态：基于 folders.updated_at（5 分钟内在线）
    upd_row = db.execute('SELECT updated_at FROM folders WHERE ip = ?', (ip,)).fetchone()
    updated_at = upd_row['updated_at'] if upd_row else None
    online = False
    if updated_at:
        try:
            upd_dt = datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S")
            online = (datetime.utcnow() - upd_dt).total_seconds() <= 70
        except Exception:
            online = False
    
    return jsonify({
        "ip": ip,
        "device_id": device_id,
        "remark": remark,
        "videos": videos,
        "last_upload_at": last_upload_at,
        "online": online,
        "upload_enabled": bool(upload_enabled),
        "webrtc_direct": bool(webrtc_direct)
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


@app.route("/api/upload/<device_id>", methods=["POST"])
def upload_video(device_id):
    """上传视频（不需登录，文件名为时间）
    path param: device_id (必须是设备的唯一 ID)
    query param: ip (可选，用于确定存储路径，不传则使用 remote_addr)
    """
    # 获取 IP 用于决定存储路径
    client_ip = request.args.get("ip") or request.remote_addr
    
    folder = folder_path(client_ip)
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
        # 记录视频文件
        file_size = os.path.getsize(save_path)
        db.execute(
            'INSERT INTO videos (ip, filename, file_size) VALUES (?, ?, ?)',
            (client_ip, filename, file_size)
        )
        # 不在上传时更新在线状态，改由心跳接口维护
        db.commit()
    except Exception as e:
        print(f"数据库记录失败: {e}")

    print(f"[UPLOAD] {request.remote_addr} 上传 {filename} -> {folder}")
    return jsonify({"filename": filename, "ip": client_ip})


# ---------------- 心跳/在线状态 API ----------------
@app.route("/api/heartbeat/<device_id>", methods=["GET"])
def heartbeat(device_id):
    """心跳：更新 folders.updated_at 和 last_upload_at
    path param: device_id (必须)
    query param: ip (可选，用于更新设备 IP，不传则使用 remote_addr)
    """
    current_ip = request.args.get("ip") or request.remote_addr
    
    db = get_db()
    try:
        # --- 核心逻辑：确保 (device_id, current_ip) 的记录存在且是最新的 ---
        
        # 1. 检查数据库是否已有该 device_id 的记录
        existing_dev = db.execute('SELECT * FROM folders WHERE device_id = ?', (device_id,)).fetchone()
        
        if existing_dev:
            old_ip = existing_dev['ip']
            if old_ip != current_ip:
                print(f"[MIGRATE] 设备 {device_id} IP 变更: {old_ip} -> {current_ip}")
                # IP 变更逻辑
                # 检查新 IP 是否已被占用 (可能是另一台机器，或者数据库脏数据)
                target_occupier = db.execute('SELECT * FROM folders WHERE ip = ?', (current_ip,)).fetchone()
                
                if target_occupier:
                    # 冲突：新 IP 被其他 device_id (或 null) 占用
                    # 策略：强制覆盖，认为当前心跳是权威的
                    print(f"[MIGRATE] 目标 IP {current_ip} 被占用，正在合并/覆盖...")
                    pass

                # 执行 IP 更新
                # 1. 更新 folders 表
                try:
                    db.execute('UPDATE folders SET ip = ?, updated_at = CURRENT_TIMESTAMP WHERE device_id = ?', (current_ip, device_id))
                except sqlite3.IntegrityError:
                    # 如果 update 失败(如 ip 冲突)，先删掉冲突的记录
                    db.execute('DELETE FROM folders WHERE ip = ? AND device_id != ?', (current_ip, device_id))
                    db.execute('UPDATE folders SET ip = ?, updated_at = CURRENT_TIMESTAMP WHERE device_id = ?', (current_ip, device_id))

                # 2. 迁移 videos 表 (把旧 IP 的视频归属到新 IP)
                db.execute('UPDATE videos SET ip = ? WHERE ip = ?', (current_ip, old_ip))
                
                # 3. 物理文件夹迁移
                try:
                    old_path = folder_path(old_ip)
                    new_path = folder_path(current_ip)
                    if os.path.exists(old_path) and old_ip != current_ip:
                        if not os.path.exists(new_path):
                            os.rename(old_path, new_path)
                            print(f"[MIGRATE] 文件夹重命名: {old_ip} -> {current_ip}")
                        else:
                            # 合并
                            for item in os.listdir(old_path):
                                s = os.path.join(old_path, item)
                                d = os.path.join(new_path, item)
                                if not os.path.exists(d):
                                    shutil.move(s, d)
                            shutil.rmtree(old_path)
                            print(f"[MIGRATE] 文件夹合并完成")
                except Exception as e:
                    print(f"[MIGRATE] 文件迁移警告: {e}")

            else:
                # IP 没变，只更新时间
                db.execute('UPDATE folders SET updated_at = CURRENT_TIMESTAMP WHERE device_id = ?', (device_id,))

        else:
            # 数据库无此 device_id 记录
            # 检查 current_ip 是否存在 (可能是旧设备升级后第一次上报 device_id，或者纯粹的 IP 冲突)
            ip_rec = db.execute('SELECT * FROM folders WHERE ip = ?', (current_ip,)).fetchone()
            if ip_rec:
                # 补全 device_id (或者覆盖旧的 device_id，以当前心跳为准)
                print(f"[BIND] 将设备 {device_id} 绑定到现有 IP {current_ip}")
                db.execute('UPDATE folders SET device_id = ?, updated_at = CURRENT_TIMESTAMP WHERE ip = ?', (device_id, current_ip))
            else:
                # 全新设备
                print(f"[NEW] 新设备接入: {device_id} @ {current_ip}")
                db.execute(
                    'INSERT INTO folders (ip, remark, upload_enabled, webrtc_direct, device_id) VALUES (?, ?, ?, ?, ?)',
                    (current_ip, "", 0, 0, device_id) # 默认 upload=0 (False)
                )

        # 更新 last_upload_at (videos 表)
        last_video = db.execute(
            'SELECT id FROM videos WHERE ip = ? ORDER BY uploaded_at DESC LIMIT 1',
            (current_ip,)
        ).fetchone()
        
        if last_video:
            db.execute(
                'UPDATE videos SET uploaded_at = CURRENT_TIMESTAMP WHERE id = ?',
                (last_video['id'],)
            )
        
        db.commit()
        
        # 返回当前状态
        row = db.execute('SELECT updated_at, upload_enabled, webrtc_direct FROM folders WHERE ip = ?', (current_ip,)).fetchone()
        return jsonify({
            "msg": "ok",
            "ip": current_ip,
            "updated_at": row['updated_at'] if row else None,
            "upload_enabled": bool(row['upload_enabled']) if row else True,
            "webrtc_direct": bool(row['webrtc_direct']) if row else False,
        })

    except Exception as e:
        print(f"数据库记录失败: {e}")
        db.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/folders/<ip>/upload_enabled", methods=["PATCH"])
@login_required
def update_upload_enabled(ip):
    """更新视频上传开关状态"""
    data = request.json or {}
    upload_enabled = data.get("upload_enabled", True)
    db = get_db()
    try:
        # 确保文件夹记录存在
        cursor = db.execute('SELECT ip FROM folders WHERE ip = ?', (ip,))
        if not cursor.fetchone():
            db.execute('INSERT INTO folders (ip, upload_enabled, webrtc_direct) VALUES (?, ?, ?)', (ip, 0, 0))
        # 更新 upload_enabled
        db.execute('UPDATE folders SET upload_enabled = ? WHERE ip = ?', 
                   (1 if upload_enabled else 0, ip))
        db.commit()
        return jsonify({"msg": "ok", "ip": ip, "upload_enabled": bool(upload_enabled)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/folders/<ip>/webrtc_direct", methods=["PATCH"])
@login_required
def update_webrtc_direct(ip):
    """更新推流直连状态（无需登录，用于控制客户端 RTMP 推流）"""
    data = request.json or {}
    webrtc_direct = data.get("webrtc_direct", False)
    db = get_db()
    try:
        # 确保文件夹记录存在
        cursor = db.execute('SELECT ip FROM folders WHERE ip = ?', (ip,))
        if not cursor.fetchone():
            db.execute('INSERT INTO folders (ip, upload_enabled, webrtc_direct) VALUES (?, ?, ?)', (ip, 0, 0))
        # 更新 webrtc_direct
        db.execute('UPDATE folders SET webrtc_direct = ? WHERE ip = ?', (1 if webrtc_direct else 0, ip))
        db.commit()
        return jsonify({"msg": "ok", "ip": ip, "webrtc_direct": bool(webrtc_direct)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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




def reset_preview_state():
    """重置所有客户端的推流状态为关闭"""
    try:
        with sqlite3.connect(DB_PATH) as db:
            # 这里的 webrtc_direct 是数据库字段名，保持不变以兼容
            db.execute('UPDATE folders SET webrtc_direct = 0')
            db.commit()
        print("✅ 已重置所有推流状态为关闭")
    except Exception as e:
        print(f"⚠️ 重置推流状态失败: {e}")


# ---------------- 启动 ----------------
if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    
    # 配置日志
    setup_logging()
    
    # 初始化数据库
    init_db()
    # 重置推流状态
    reset_preview_state()
    
    # 启动 HLS 预览服务子进程
    p = Process(target=start_preview_server, daemon=True)
    p.start()
    
    print("✅ HLS 预览服务已启动 (port 5002)")
    print("✅ 数据库初始化完成")
    print("✅ 后端 API 服务启动 (port 5001)")
    print("📝 访问: http://5001/frontend/login.html")
    # 重要：Windows 下禁用 reloader，避免重复启动子进程导致套接字异常
    app.run(host="0.0.0.0", port=5001, debug=True, use_reloader=False)

