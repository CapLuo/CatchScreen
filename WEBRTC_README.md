# WebRTC 推流系统使用指南

## 概述

这是一个基于 Python 和 aiortc 的 WebRTC 屏幕推流系统，支持：
- 客户端（PC 应用）屏幕抓取和推流
- 服务端接收推流并提供网页预览
- 自动重连机制
- 多客户端连接支持

## 系统架构

```
客户端 (pc_video_track.py)  --WebRTC推流-->  服务端 (webrtc_server.py)  --网页预览-->  浏览器
```

## 依赖安装

### 服务端依赖
```bash
pip install aiortc aiohttp requests
```

### 客户端依赖
```bash
pip install aiortc mss av numpy opencv-python pillow requests aiohttp
```

### 完整依赖（推荐）
```bash
pip install aiortc aiohttp mss av numpy opencv-python pillow requests
```

## 快速开始

### 1. 启动服务端

```bash
python webrtc_server.py
```

服务端默认监听 `5002` 端口，启动后会输出：
```
🚀 启动 WebRTC 服务器 (port 5002)
```

### 2. 启动客户端

```bash
python pc_video_track.py --file_server http://127.0.0.1:5001 --rtc_server http://127.0.0.1:5002 --fps 15
```

参数说明：
- `--file_server`: 后端文件服务器地址（用于心跳和上传）
- `--rtc_server`: WebRTC 服务器地址
- `--fps`: 推流帧率（默认 15）
- `--reconnect-delay`: 重连延迟（默认 5.0 秒）
- `--check-interval`: 状态检查间隔（默认 2.0 秒）

### 3. 打开网页预览

在浏览器中访问：
```
http://127.0.0.1:5002/preview?ip=客户端IP
```

例如：
```
http://127.0.0.1:5002/preview?ip=192.168.1.100
```

## API 接口

### 服务端接口

#### POST /offer
客户端推流接口，接收 SDP Offer，返回 SDP Answer

**请求体：**
```json
{
  "sdp": "...",
  "type": "offer"
}
```

**响应：**
```json
{
  "sdp": "...",
  "type": "answer"
}
```

#### POST /view
网页预览接口，接收 SDP Offer，返回 SDP Answer

**请求体：**
```json
{
  "sdp": "...",
  "type": "offer",
  "timeout": 0
}
```

**响应：**
```json
{
  "sdp": "...",
  "type": "answer"
}
```

#### POST /viewer/open
通知服务端观众进入

**请求体：**
```json
{
  "ip": "192.168.1.100"
}
```

#### POST /viewer/close
通知服务端观众离开

**请求体：**
```json
{
  "ip": "192.168.1.100"
}
```

#### GET /preview
返回预览页面 HTML

## 自动重连机制

### 客户端重连逻辑

1. **连接断开检测**：客户端监听 `connectionState` 变化
   - 当状态变为 `failed`、`closed` 或 `disconnected` 时触发重连

2. **服务器检测**：客户端会循环检测服务器状态
   - 检查后端服务器的 `webrtc_direct` 状态
   - 检查 WebRTC 服务器是否可访问
   - 当 `webrtc_direct=true` 时开始推流

3. **重连流程**：
   ```
   连接断开 → 等待 reconnect_delay 秒 → 检查服务器状态 → 重新创建连接 → 推流
   ```

### 服务端处理

- 服务端支持多客户端连接，使用 `set()` 保存所有 PeerConnection
- 使用 `MediaRelay` 实现流的复用，多个观众可以观看同一个发布者的流
- 当观众离开时，可以选择关闭所有连接或仅关闭观众连接

## 配置说明

### 环境变量

- `BACKEND_BASE`: 后端服务器地址（默认：`http://127.0.0.1:5001`）
- `WEBRTC_PORT`: WebRTC 服务器端口（默认：`5002`）

### 客户端配置

可以通过命令行参数或修改代码中的默认值来配置：
- 帧率（fps）
- 重连延迟（reconnect_delay）
- 状态检查间隔（check_interval）

## 日志说明

### 服务端日志

- `[INFO] [WebRTC-PUBLISHER]`: 发布者连接相关日志
- `[INFO] [WebRTC-VIEWER]`: 观众连接相关日志
- `[ERROR]`: 错误日志

### 客户端日志

- `[webrtc]`: WebRTC 连接状态
- `[UPLOAD]`: 文件上传相关
- `[INFO]`: 一般信息
- `[ERROR]`: 错误信息

## 故障排查

### 1. 客户端无法连接服务端

- 检查服务端是否启动
- 检查端口是否正确（默认 5002）
- 检查防火墙设置

### 2. 网页预览无画面

- 确认客户端已成功推流
- 检查浏览器控制台是否有错误
- 确认客户端 IP 参数正确

### 3. 自动重连不工作

- 检查客户端日志，确认是否检测到连接断开
- 检查 `webrtc_direct` 状态是否正确
- 检查网络连接是否稳定

## 性能优化

1. **帧率调整**：根据网络带宽调整 `--fps` 参数
2. **分辨率**：客户端代码中可调整屏幕抓取分辨率
3. **重连延迟**：根据实际情况调整 `--reconnect-delay`

## 注意事项

1. 客户端需要屏幕录制权限（Windows/Mac/Linux）
2. 服务端需要开放相应端口（默认 5002）
3. 网页预览需要浏览器支持 WebRTC
4. 建议在内网环境下使用，公网使用需要配置 TURN 服务器

## 开发说明

### 代码结构

- `webrtc_server.py`: WebRTC 服务端，使用 aiohttp
- `pc_video_track.py`: 客户端推流程序
- 所有函数都有类型注解和文档字符串

### 扩展功能

- 可以添加音频推流支持
- 可以添加录制功能
- 可以添加多显示器支持
- 可以添加 TURN 服务器配置

## 许可证

本项目遵循项目原有许可证。

