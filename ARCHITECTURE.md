# 架构说明

## 前后端分离架构

本项目采用前后端分离的设计模式：

```
┌─────────────────────────────────────┐
│         前端 (Frontend)             │
├─────────────────────────────────────┤
│  - login.html    (登录页面)          │
│  - index.html    (文件夹列表)        │
│  - folder.html   (视频详情)          │
│                                     │
│  技术栈: HTML + JavaScript          │
│  UI框架: Bootstrap 5               │
└─────────────────────────────────────┘
              ↕ HTTP/AJAX
┌─────────────────────────────────────┐
│        后端 API (Backend)            │
├─────────────────────────────────────┤
│  backend.py                         │
│  ├── /api/login        (登录API)     │
│  ├── /api/folders      (文件夹API)   │
│  ├── /api/upload       (上传API)     │
│  ├── /uploads/:ip/:file (视频文件)    │
│  └── /frontend/*       (静态文件)    │
│                                     │
│  技术栈: Flask + Flask-CORS         │
│  数据库: 内存字典 (可升级)           │
└─────────────────────────────────────┘
              ↕
┌─────────────────────────────────────┐
│      WebRTC 服务器                  │
├─────────────────────────────────────┤
│  webrtc_server.py                   │
│  ├── /webrtc           (WebRTC连接)  │
│                                     │
│  端口: 8080                         │
└─────────────────────────────────────┘
```

## 数据流

### 1. 用户登录流程

```
用户浏览器 → 前端(login.html) 
           ↓ 提交表单
        AJAX POST /api/login
           ↓
        后端验证账号密码
           ↓
      设置 Session Cookie
           ↓
       返回成功响应
           ↓
      前端跳转到 index.html
```

### 2. 获取文件夹列表

```
用户浏览器 → 前端(index.html)
           ↓
        AJAX GET /api/folders (带上Cookie)
           ↓
        后端验证登录状态
           ↓
        扫描 uploads/ 目录
           ↓
        返回文件夹列表JSON
           ↓
        前端渲染卡片列表
```

### 3. 视频上传流程

```
客户端设备 → POST /api/upload/:ip
           ↓
        后端接收文件
           ↓
      生成时间戳文件名
           ↓
      保存到 uploads/:ip/
           ↓
        返回文件名
```

## API 接口设计

### 认证接口

| 方法 | 路径 | 说明 | 需要登录 |
|------|------|------|---------|
| POST | /api/login | 登录 | 否 |
| POST | /api/logout | 退出 | 是 |
| GET | /api/check_login | 检查状态 | 否 |

### 文件夹接口

| 方法 | 路径 | 说明 | 需要登录 |
|------|------|------|---------|
| GET | /api/folders | 获取列表 | 是 |
| GET | /api/folders/:ip | 获取详情 | 是 |
| POST | /api/folders | 创建文件夹 | 是 |
| PATCH | /api/folders/:ip/remark | 修改备注 | 是 |
| DELETE | /api/folders/:ip | 删除文件夹 | 是 |

### 视频接口

| 方法 | 路径 | 说明 | 需要登录 |
|------|------|------|---------|
| GET | /uploads/:ip/:file | 获取视频 | 是 |
| POST | /api/upload/:ip | 上传视频 | 否 |

## 安全设计

### 会话管理

- 使用 Flask 的 Session 机制
- Session 存储在服务端
- 通过 HTTP Cookie 传递 Session ID

### 跨域处理

- 使用 Flask-CORS 启用跨域支持
- 允许前端从不同域访问 API
- 支持携带认证信息 (credentials)

### 访问控制

- 所有管理接口需要登录认证
- 视频上传接口开放（方便外部系统调用）
- 视频播放需要登录验证

## 扩展建议

### 1. 数据库升级

当前使用内存字典，建议升级为：

- **SQLite**: 轻量级，适合小型项目
- **PostgreSQL**: 功能完整，适合生产环境
- **Redis**: 高速缓存，适合高并发

### 2. 认证升级

当前使用 Session，可以升级为：

- **JWT Token**: 无状态认证
- **OAuth2**: 第三方登录
- **双因素认证**: 提高安全性

### 3. 文件存储

当前使用本地文件系统，可以升级为：

- **对象存储**: AWS S3, 阿里云 OSS
- **CDN 加速**: 提高视频加载速度
- **视频转码**: 自动转换为不同清晰度

### 4. 前端框架

可以升级为：

- **Vue.js / React**: 单页应用
- **TypeScript**: 类型安全
- **PWA**: 离线支持

### 5. 部署方案

- **Docker**: 容器化部署
- **Nginx**: 反向代理和负载均衡
- **Gunicorn**: WSGI 服务器
- **Supervisor**: 进程管理

## 目录结构

```
Screen/
├── backend.py              # 后端 API 服务
├── webrtc_server.py        # WebRTC 服务器
├── main.py                 # 原版（未分离版本）
├── requirements.txt        # Python 依赖
├── README.md              # 使用说明
├── ARCHITECTURE.md        # 架构说明（本文件）
├── start.bat              # Windows 启动脚本
├── start.sh               # Linux/Mac 启动脚本
├── .gitignore             # Git 忽略文件
│
├── uploads/               # 视频存储目录
│   └── IP地址/             # 按IP自动分文件夹
│       └── 视频文件.mp4
│
└── frontend/              # 前端文件
    ├── login.html         # 登录页面
    ├── index.html         # 文件夹列表页
    └── folder.html        # 视频详情页
```

## 核心技术

- **后端**: Flask 微框架，轻量级快速开发
- **CORS**: 解决跨域问题，支持前后端分离
- **Session**: 服务端会话管理
- **WebRTC**: 实时视频通信（可选功能）
- **Bootstrap**: 响应式 UI 框架
- **HTML5 Video**: 浏览器原生视频播放

## 性能优化

1. **静态文件缓存**: 使用 Nginx 缓存前端文件
2. **视频流式传输**: 支持视频分块加载
3. **图片缩略图**: 生成视频缩略图预览
4. **CDN 加速**: 视频文件使用 CDN 分发
5. **数据库索引**: 文件夹和视频信息索引优化

