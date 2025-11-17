# WebRTC → RTMP + HLS 重构说明

## 📋 重构概述

本次重构将原有的 **WebRTC (aiortc)** 推流模式替换为 **RTMP + HLS** 被动预览模式，保持前端和客户端接口不变。

## ✅ 保留的内容

1. **前端代码**：完全不变
   - 预览接口路径：`/preview?ip=xxx`
   - 所有按钮和交互逻辑
   - JavaScript 调用方式

2. **客户端逻辑**：完全不变
   - 心跳轮询：`/api/heartbeat/<ip>`
   - 推流状态判断：`webrtc_direct` 字段
   - 视频上传接口：`/api/upload/<ip>`

3. **后端 API**：完全保留
   - 所有文件夹管理接口
   - 视频上传接口
   - 心跳接口
   - `webrtc_direct` 状态控制接口

## 🔄 替换的部分

### 1. `webrtc_server.py` - 完全重写

**原功能**：WebRTC 推流服务器（使用 aiortc）
**新功能**：HLS 预览服务器（被动检测 HLS 文件）

**主要变更**：
- ❌ 删除所有 `aiortc` 相关代码
- ❌ 删除 `/offer`、`/view`、`/viewer/open`、`/viewer/close` 接口
- ✅ 新增 `/preview` 接口：返回 HLS 播放页面
- ✅ 新增 `/hls/{ip}/{filename}` 接口：提供 HLS 文件访问
- ✅ 新增 `/status` 接口：查看活跃的 HLS 流
- ✅ 新增 HLS 检测逻辑：检查 `/var/www/hls/{ip}/index.m3u8`

### 2. `backend.py` - 最小修改

**变更内容**：
- 保留 `webrtc_server` 启动逻辑（兼容性）
- 更新注释：将 "WebRTC" 改为 "HLS 预览服务"
- 保留 `webrtc_direct` 接口（客户端仍需要使用）

### 3. `requirements.txt` - 依赖更新

**删除**：
- `aiortc>=1.6.0`

**保留**：
- `aiohttp>=3.9.0`（用于 HLS 预览服务器）

## 📝 代码 Diff

### webrtc_server.py - 完全重写

```diff
- """WebRTC 推流服务器""" 
- import aiortc
- from aiortc import RTCPeerConnection, RTCSessionDescription
- from aiortc.contrib.media import MediaRelay
+ """HLS 预览服务器"""
+ import os
+ from aiohttp import web
+ from aiohttp.web import Request, Response
+
+ # 配置
+ SERVER_PORT = int(os.environ.get('WEBRTC_PORT', '5002'))
+ HLS_ROOT = os.environ.get('HLS_ROOT', '/var/www/hls')
```

**新增函数**：
- `check_hls_exists(ip: str) -> bool`: 检查 HLS 文件是否存在
- `get_hls_url(ip: str) -> Optional[str]`: 获取 HLS 播放地址
- `handle_hls_file(request: Request) -> Response`: 提供 HLS 文件访问

**删除函数**：
- `handle_offer()`: WebRTC Offer 处理
- `handle_view()`: WebRTC View 处理
- `handle_viewer_open()`: 观众进入通知
- `handle_viewer_close()`: 观众离开通知
- 所有 `RTCPeerConnection` 相关逻辑

**修改函数**：
- `handle_preview()`: 返回 HLS 播放页面（HTML 中包含 HLS.js 客户端）

### backend.py - 最小修改

```diff
  @app.route("/api/folders/<ip>/webrtc_direct", methods=["PATCH"])
  def update_webrtc_direct(ip):
-     """更新 WebRTC 直连状态（无需登录，供 webrtc_server 调用）"""
+     """更新推流直连状态（无需登录，用于控制客户端 RTMP 推流）"""
      ...

  # 启动 HLS 预览服务子进程
  p = Process(target=start_webrtc_server, daemon=True)
  p.start()
  
- print("✅ WebRTC 服务已启动 (port 5002)")
+ print("✅ HLS 预览服务已启动 (port 5002)")
```

## 🔧 依赖变更

### 删除的依赖
- `aiortc>=1.6.0`（WebRTC 库）

### 保留的依赖
- `aiohttp>=3.9.0`（异步 HTTP 服务器，用于 HLS 预览服务）

### 新增的系统依赖（部署时需要）

**RTMP 服务器**（客户端推流需要）：
- Nginx with RTMP module（推荐）
- 或 FFmpeg + RTMP 服务器

**配置示例**（nginx.conf）：
```nginx
rtmp {
    server {
        listen 1935;
        chunk_size 4096;
        
        application live {
            live on;
            record off;
            
            # 转换为 HLS
            hls on;
            hls_path /var/www/hls;
            hls_fragment 2s;
            hls_playlist_length 10s;
            
            # 按 IP 分组
            on_publish http://127.0.0.1:5001/api/rtmp/publish;
        }
    }
}
```

**HLS 文件目录**：
- 默认路径：`/var/www/hls/{ip}/index.m3u8`
- 可通过环境变量 `HLS_ROOT` 自定义

## 🚀 部署步骤

### 1. 更新依赖

```bash
pip uninstall aiortc
pip install -r requirements.txt
```

### 2. 配置 RTMP 服务器

客户端推流地址：`rtmp://your-server-ip:1935/live/{ip}`

服务器会将 RTMP 流转换为 HLS，保存到：`/var/www/hls/{ip}/index.m3u8`

### 3. 配置 HLS 根目录（可选）

```bash
export HLS_ROOT=/var/www/hls
```

### 4. 启动服务

```bash
python backend.py
```

服务会自动启动：
- Flask API 服务（端口 5001）
- HLS 预览服务（端口 5002）

### 5. 客户端推流

客户端程序（需要修改为 RTMP 推流）：
```python
# 使用 ffmpeg 推流示例
import subprocess
import os

def push_rtmp_stream(rtmp_url: str, fps: int = 15):
    cmd = [
        'ffmpeg',
        '-f', 'gdigrab',
        '-framerate', str(fps),
        '-i', 'desktop',
        '-c:v', 'libx264',
        '-preset', 'ultrafast',
        '-tune', 'zerolatency',
        '-f', 'flv',
        rtmp_url
    ]
    subprocess.run(cmd)

# 推流地址
rtmp_url = f"rtmp://8.134.173.118:1935/live/{get_ip_address()}"
push_rtmp_stream(rtmp_url)
```

## 📊 工作流程

### 原 WebRTC 流程
```
客户端 → WebRTC Offer → 服务器 Answer → 建立连接 → 推流
前端 → 预览页面 → WebRTC View → 服务器 Answer → 播放
```

### 新 RTMP + HLS 流程
```
客户端 → RTMP 推流 → Nginx 转换为 HLS → 保存到文件系统
前端 → 预览页面 → 轮询检测 HLS 文件 → HLS.js 播放
```

## 🔍 预览接口说明

### GET /preview?ip=xxx

**返回**：HLS 播放页面（HTML）

**页面功能**：
1. 自动检测 HLS 流：`/hls/{ip}/index.m3u8`
2. 轮询检测：每秒检测一次，最多 300 次（约 5 分钟）
3. 自动播放：检测到流后自动开始播放
4. 等待状态：显示"等待客户端推流..."提示

### GET /hls/{ip}/index.m3u8

**返回**：HLS 播放列表文件（m3u8）

### GET /hls/{ip}/{segment}.ts

**返回**：HLS 视频片段文件（ts）

### GET /status

**返回**：服务器状态 JSON
```json
{
  "hls_root": "/var/www/hls",
  "active_streams_count": 2,
  "active_streams": [
    {
      "ip": "192.168.1.100",
      "has_playlist": true,
      "latest_segment_time": 1234567890.0
    }
  ]
}
```

## ⚠️ 注意事项

1. **HLS 延迟**：HLS 有约 6-10 秒延迟（取决于 `hls_fragment` 和 `hls_playlist_length` 配置）
2. **文件权限**：确保 `/var/www/hls` 目录可写
3. **Nginx 配置**：需要安装 `nginx-rtmp-module`
4. **客户端修改**：客户端需要改为 RTMP 推流（不在本次重构范围内）
5. **兼容性**：前端代码无需修改，预览接口路径保持不变

## 📌 待办事项

- [ ] 客户端修改为 RTMP 推流（需要单独处理）
- [ ] 配置 Nginx RTMP 服务器
- [ ] 测试 HLS 流播放
- [ ] 监控 HLS 文件生成

## 📚 参考

- [HLS.js 文档](https://github.com/video-dev/hls.js/)
- [Nginx RTMP Module](https://github.com/arut/nginx-rtmp-module)
- [FFmpeg RTMP 推流](https://ffmpeg.org/ffmpeg-protocols.html#rtmp)
