# 视频上传管理系统 - 前后端分离版

## 📋 项目说明

本项目是一个视频上传管理系统，采用前后端分离的架构设计。

### 架构特点

- **后端**: Flask RESTful API 服务 + SQLite 数据库
- **前端**: 纯 HTML + JavaScript（Bootstrap 5.3）
- **数据库**: SQLite 轻量级数据库
- **功能**: 管理员登录、按IP自动建文件夹、视频上传、HLS 实时预览等

## 🚀 快速开始

### Windows 用户

双击运行 `start.bat` 文件即可启动服务

### Mac/Linux 用户

```bash
chmod +x start.sh
./start.sh
```

### 手动启动

#### 1. 安装依赖

```bash
pip install -r requirements.txt
```

#### 2. 启动后端服务

```bash
python backend.py
```

后端服务将在 `http://127.0.0.1:5001` 启动，提供 REST API 接口。

同时会自动启动 HLS 预览服务（端口 5002）用于直播点看功能。

#### 3. 访问前端

在浏览器中打开：

- **登录页面**: `http://127.0.0.1:5001/frontend/login.html`

### 4. 默认账号

- **用户名**: `admin`
- **密码**: `123456`

## 📁 项目结构

```
Screen/
├── backend.py              # 后端 API 服务（Flask + SQLite）
├── preview_server.py       # HLS 预览服务器 (aiohttp)
├── pc_video_track.py       # PC 客户端推流程序
├── db_manage.py            # 数据库管理工具
├── requirements.txt        # Python 依赖
├── database.db             # SQLite 数据库（自动创建）
├── README.md               # 说明文档
├── uploads/                # 视频存储目录（自动创建）
│   └── IP地址/             # 按IP自动分文件夹
│       └── 视频文件.mp4
└── frontend/               # 前端文件
    ├── login.html          # 登录页面
    ├── index.html          # 文件夹列表页
    └── folder.html         # 视频详情页
```

## 📺 客户端推流 (HLS)

使用 `pc_video_track.py` 进行屏幕推流：

```bash
# 依赖安装
pip install mss numpy opencv-python pillow requests

# 启动推流
python pc_video_track.py --file_server http://127.0.0.1:5001 --mode hls --hls_dir ./hls_output --fps 15
```
*(注：需安装 FFmpeg 并配置环境变量)*

## 🔧 API 接口说明

### 认证相关

- `POST /api/login` - 登录
- `POST /api/logout` - 退出登录
- `GET /api/check_login` - 检查登录状态

### 文件夹管理

- `GET /api/folders` - 获取所有文件夹列表
- `POST /api/folders` - 创建文件夹
- `GET /api/folders/<ip>` - 获取文件夹详情
- `PATCH /api/folders/<ip>/remark` - 修改备注
- `DELETE /api/folders/<ip>` - 删除文件夹

### 视频管理

- `GET /uploads/<ip>/<filename>` - 获取视频文件
- `POST /api/upload/<ip>` - 上传视频

## 🎯 主要功能

- ✅ 管理员登录/注销
- ✅ 按 IP 自动建文件夹存放视频
- ✅ 修改备注名
- ✅ 删除文件夹
- ✅ 浏览器查看、在线播放
- ✅ 视频上传
- ✅ 响应式设计，支持移动端

## 🗄️ 数据库管理
## 🐳 Docker 部署

### 使用 Docker Compose（推荐）

1) 构建并启动

```bash
docker compose up -d --build
```

2) 访问

- 后端接口与前端页面: http://127.0.0.1:5001/frontend/login.html
- HLS 预览页: http://127.0.0.1:5002/preview?ip=YOUR_IP

3) 数据持久化

- 上传目录挂载到宿主机 `./uploads`
- SQLite 数据库使用宿主机 `./database.db`

4) 停止/查看日志

```bash
docker compose logs -f
docker compose down
```

### 直接使用 Docker

```bash
docker build -t catchscreen:latest .
docker run -d --name catchscreen \
  -p 5001:5001 -p 5002:5002 \
  -v $PWD/uploads:/app/uploads \
  -v $PWD/database.db:/app/database.db \
  -v $PWD/frontend:/app/frontend \
  catchscreen:latest
```

> 注意：`preview_server.py` 端口为 5002，如需自定义端口请同步修改 `docker-compose.yml` 的端口映射。


### 管理工具

项目提供了数据库管理工具 `db_manage.py`：

```bash
# 初始化数据库
python db_manage.py init

# 查看所有数据
python db_manage.py list

# 显示统计信息
python db_manage.py stats

# 清空数据库
python db_manage.py clear
```

### 数据库结构

- **folders 表**: 存储文件夹信息（IP、备注、创建时间等）
- **videos 表**: 存储视频文件信息（文件名、大小、上传时间等）

数据库会在首次启动时自动创建（`database.db` 文件）。

## 🔄 从旧版迁移

如果你之前使用的是 `main.py`（未分离版），可以：

1. 继续使用 `main.py` - 功能不变
2. 迁移到新版本 `backend.py` - 前后端分离架构 + SQLite 数据库

新版本会自动创建数据库并兼容旧数据。

## 📝 注意事项

1. 视频上传接口 `/api/upload/<ip>` 不需要登录验证，方便外部系统调用
2. 其他接口需要登录后使用
3. 视频文件按 IP 地址自动分类存储
4. 支持 `.mp4`, `.avi`, `.mov`, `.webm`, `.mkv` 等视频格式

## 🔐 安全说明

- 登录使用 Flask 的 session 管理
- 建议在生产环境中修改默认的用户名和密码
- 建议使用 HTTPS
- 可以为上传接口添加访问限制

## 💡 技术栈

- **后端**: Flask, Flask-CORS, SQLite
- **数据库**: SQLite（轻量级嵌入式数据库）
- **前端**: Bootstrap 5.3, Bootstrap Icons
- **视频**: HTML5 Video
- **通信**: WebRTC (可选)

## 📄 许可证

MIT License

