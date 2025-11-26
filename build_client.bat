@echo off
echo [BUILD] Starting PyInstaller build for CatchScreenClient...

:: 1. 清理旧的构建文件
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
if exist CatchScreenClient.spec del CatchScreenClient.spec

:: 2. 检查 ffmpeg 是否存在
if not exist "ffmpeg\bin\ffmpeg.exe" (
    echo [ERROR] ffmpeg.exe not found at ffmpeg\bin\ffmpeg.exe!
    echo Please download ffmpeg and place it in the ffmpeg/bin folder.
    pause
    exit /b 1
)

:: 3. 执行打包命令
:: --noconfirm: 不询问覆盖
:: --onefile: 打包成单文件
:: --console: 显示控制台 (如果想隐藏黑框，改为 --windowed)
:: --add-binary: 将本地 ffmpeg/bin/ffmpeg.exe 打包进 exe 的 ffmpeg/bin 目录
pyinstaller --noconfirm --onefile --console --name "CatchScreenClient" ^
    --add-binary "ffmpeg/bin/ffmpeg.exe;ffmpeg/bin" ^
    pc_video_track.py

if %errorlevel% neq 0 (
    echo [ERROR] Build failed!
    pause
    exit /b %errorlevel%
)

echo [SUCCESS] Build finished! Executable is in dist/CatchScreenClient.exe
pause
