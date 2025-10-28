# 快速测试指南

## 📦 项目拆分完成

您的项目已成功拆分为前后端分离架构！

## ✅ 完成的工作

1. ✅ 创建独立的后端 API 服务 (`backend.py`)
2. ✅ 创建前端页面 (`frontend/` 目录)
3. ✅ 添加 CORS 支持，允许跨域访问
4. ✅ 创建 WebRTC 服务器 (`webrtc_server.py`)
5. ✅ 添加静态文件服务支持
6. ✅ 创建启动脚本 (`start.bat`, `start.sh`)
7. ✅ 编写完整文档 (`README.md`, `ARCHITECTURE.md`)

## 🧪 测试步骤

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 启动服务

**Windows:**
```bash
start.bat
```

**Linux/Mac:**
```bash
./start.sh
```

### 3. 访问系统

打开浏览器访问: http://127.0.0.1:5000/frontend/login.html

登录信息:
- 用户名: `admin`
- 密码: `123456`

## 🎯 功能测试清单

### 基础功能
- [ ] 登录功能
- [ ] 查看文件夹列表
- [ ] 查看文件夹详情
- [ ] 在线播放视频

### 上传功能
测试视频上传，可以用以下方法：

**使用 curl:**
```bash
curl -X POST http://127.0.0.1:5000/api/upload/192.168.1.100 \
  -F "file=@test.mp4"
```

**使用 Python:**
```python
import requests

files = {'file': open('test.mp4', 'rb')}
response = requests.post('http://127.0.0.1:5000/api/upload/192.168.1.100', files=files)
print(response.json())
```

**使用 HTML 表单:**
创建一个测试页面：

```html
<form action="http://127.0.0.1:5000/api/upload/192.168.1.100" method="post" enctype="multipart/form-data">
  <input type="file" name="file">
  <button type="submit">上传</button>
</form>
```

## 📊 对比：原版 vs 分离版

### 原版 (`main.py`)
- ❌ 前后端耦合
- ❌ 使用 Flask 模板渲染
- ❌ HTML 嵌入在 Python 代码中
- ✅ 简单直接

### 分离版 (`backend.py`)
- ✅ 前后端完全分离
- ✅ 纯 API 服务
- ✅ 独立的前端文件
- ✅ 易于维护和扩展
- ✅ 可以独立部署
- ✅ 支持多端访问

## 🚀 部署建议

### 开发环境
- 直接运行 `python backend.py`
- 前端通过后端提供静态文件

### 生产环境
- 后端部署到服务器（如 Gunicorn + Nginx）
- 前端可以：
  - 继续由后端提供静态文件
  - 部署到 CDN
  - 部署到独立的静态文件服务器

## 🔧 自定义配置

### 修改端口
编辑 `backend.py`:

```python
app.run(host="0.0.0.0", port=5000, debug=True)  # 修改 port
```

### 修改登录账号
编辑 `backend.py`:

```python
ADMIN_USER = "admin"   # 修改用户名
ADMIN_PASS = "123456"  # 修改密码
```

### 修改 API 地址
编辑 `frontend/*.html`:

```javascript
const API_BASE = 'http://127.0.0.1:5000/api';  // 修改为实际地址
```

## 🐛 常见问题

### 1. 前端无法访问 API
- 检查后端服务是否启动
- 检查 CORS 配置是否正确
- 检查请求地址是否正确

### 2. 视频无法播放
- 检查视频格式是否支持
- 检查登录状态
- 检查文件路径是否正确

### 3. 上传失败
- 检查文件大小限制
- 检查磁盘空间
- 检查上传接口地址

## 📝 下一步

1. 根据需求调整 UI 样式
2. 添加更多功能（如批量操作、搜索等）
3. 优化性能（缓存、CDN等）
4. 添加日志和监控
5. 完善错误处理

## 🎉 完成！

您现在拥有一个完整的前后端分离架构！

如有问题，请查看 `README.md` 和 `ARCHITECTURE.md`。

