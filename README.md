# 视频上传管理系统 - 前后端分离版

## 📋 项目说明

本项目是一个视频上传管理系统，采用前后端分离的架构设计。

### 架构特点

- **后端**: Flask RESTful API 服务 + SQLite 数据库
- **前端**: 纯 HTML + JavaScript（Bootstrap 5.3）
- **数据库**: SQLite 轻量级数据库
- **功能**: 管理员登录、按IP自动建文件夹、视频上传、在线播放等

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

后端服务将在 `http://127.0.0.1:5000` 启动，提供 REST API 接口。

同时会自动启动 WebRTC 服务（端口 8080）用于直播点看功能。

#### 3. 访问前端

在浏览器中打开：

- **登录页面**: `http://127.0.0.1:5000/frontend/login.html`

### 4. 默认账号

- **用户名**: `admin`
- **密码**: `123456`

## 📁 项目结构

```
Screen/
├── backend.py              # 后端 API 服务（SQLite 数据库）
├── webrtc_server.py        # WebRTC 服务器
├── db_manage.py            # 数据库管理工具
├── main.py                 # 原版（未分离版本）
├── requirements.txt        # Python 依赖
├── database.db             # SQLite 数据库（自动创建）
├── README.md              # 说明文档
├── uploads/               # 视频存储目录（自动创建）
│   └── IP地址/             # 按IP自动分文件夹
│       └── 视频文件.mp4
└── frontend/              # 前端文件
    ├── login.html         # 登录页面
    ├── index.html         # 文件夹列表页
    └── folder.html        # 视频详情页
```

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

