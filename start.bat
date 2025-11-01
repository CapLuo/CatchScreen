@echo off
echo ====================================
echo 启动视频管理系统（前后端分离版）
echo ====================================
echo.
echo 检查依赖...
pip show flask flask-cors >nul 2>&1
if errorlevel 1 (
    echo 正在安装依赖...
    pip install -r requirements.txt
)
echo.
echo 启动后端服务...
echo 访问地址: http://127.0.0.1:5000/frontend/login.html
echo.
python backend.py
pause

