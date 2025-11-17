"""
HLS 预览服务器
--------------
提供 HLS 视频流预览服务，替代原有的 WebRTC 推流模式
- 检测 HLS 文件是否存在
- 返回 HLS 播放页面或等待状态
"""
import os
from typing import Optional
from aiohttp import web
from aiohttp.web import Request, Response

# 配置
SERVER_PORT = int(os.environ.get('WEBRTC_PORT', '5002'))
HLS_ROOT = os.environ.get('HLS_ROOT', '/var/www/hls')  # HLS 文件根目录

# HLS 播放页面 HTML 模板
HLS_PREVIEW_HTML = """<!doctype html>
<html lang=zh-CN>
<head>
  <meta charset=utf-8>
  <meta name=viewport content="width=device-width,initial-scale=1">
  <title>HLS 实时预览</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
  <style>
    body { background:#0b0d10; color:#eaecef; margin:0; padding:0; }
    .video-container { position: relative; width: 100%; height: 100vh; display: flex; align-items: center; justify-content: center; }
    video { width:100%; max-width:100%; max-height:100vh; border-radius: 10px; background:#111; }
    video:-webkit-full-screen { width: 100vw; height: 100vh; max-width: 100vw; max-height: 100vh; border-radius: 0; }
    video:-moz-full-screen { width: 100vw; height: 100vh; max-width: 100vw; max-height: 100vh; border-radius: 0; }
    video:-ms-fullscreen { width: 100vw; height: 100vh; max-width: 100vw; max-height: 100vh; border-radius: 0; }
    video:fullscreen { width: 100vw; height: 100vh; max-width: 100vw; max-height: 100vh; border-radius: 0; }
    .fullscreen-btn { position: absolute; top: 10px; right: 10px; z-index: 1000; background: rgba(0,0,0,0.7); color: white; border: none; padding: 8px 12px; border-radius: 5px; cursor: pointer; font-size: 18px; line-height: 1; }
    .fullscreen-btn:hover { background: rgba(0,0,0,0.9); }
    .fullscreen-btn:active { transform: scale(0.95); }
    .fullscreen-btn span { display: inline-block; }
    .status-message { position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); background: rgba(0,0,0,0.8); color: white; padding: 20px 40px; border-radius: 10px; z-index: 1000; text-align: center; }
    .status-message h4 { margin: 0 0 10px 0; }
  </style>
</head>
<body>
  <div class="video-container">
    <video id="player" autoplay playsinline controls></video>
    <button class="fullscreen-btn" id="fullscreenBtn" onclick="toggleFullscreen()" title="全屏 (F11)">
      <span id="fullscreenIcon">⛶</span>
    </button>
    <div id="statusMessage" class="status-message" style="display: none;">
      <h4 id="statusTitle">等待客户端推流...</h4>
      <p id="statusText">正在检测 HLS 流...</p>
    </div>
  </div>

  <script>
const player = document.getElementById('player');
const fullscreenBtn = document.getElementById('fullscreenBtn');
const fullscreenIcon = document.getElementById('fullscreenIcon');
const statusMessage = document.getElementById('statusMessage');
const statusTitle = document.getElementById('statusTitle');
const statusText = document.getElementById('statusText');

const urlParams = new URLSearchParams(location.search);
const ip = urlParams.get('ip') || '-';
const hlsUrl = `/hls/${ip}/index.m3u8`;

let hls = null;
let checkInterval = null;
let retryCount = 0;
const MAX_RETRIES = 300; // 最多重试 300 次（约 5 分钟）

// 全屏功能
function toggleFullscreen() {
  try {
    if (!document.fullscreenElement && !document.webkitFullscreenElement && !document.mozFullScreenElement && !document.msFullscreenElement) {
      const videoContainer = player.parentElement;
      if (videoContainer.requestFullscreen) {
        videoContainer.requestFullscreen().catch(err => {
          console.log('容器全屏失败，尝试视频元素全屏:', err);
          requestVideoFullscreen();
        });
      } else if (videoContainer.webkitRequestFullscreen) {
        videoContainer.webkitRequestFullscreen();
      } else if (videoContainer.mozRequestFullScreen) {
        videoContainer.mozRequestFullScreen();
      } else if (videoContainer.msRequestFullscreen) {
        videoContainer.msRequestFullscreen();
      } else {
        requestVideoFullscreen();
      }
    } else {
      exitFullscreen();
    }
  } catch (error) {
    console.error('全屏操作失败:', error);
    requestVideoFullscreen();
  }
}

function requestVideoFullscreen() {
  if (player.requestFullscreen) {
    player.requestFullscreen().catch(err => console.error('视频全屏失败:', err));
  } else if (player.webkitRequestFullscreen) {
    player.webkitRequestFullscreen();
  } else if (player.webkitEnterFullscreen) {
    player.webkitEnterFullscreen();
  } else if (player.mozRequestFullScreen) {
    player.mozRequestFullScreen();
  } else if (player.msRequestFullscreen) {
    player.msRequestFullscreen();
  }
}

function exitFullscreen() {
  if (document.exitFullscreen) {
    document.exitFullscreen().catch(err => console.error('退出全屏失败:', err));
  } else if (document.webkitExitFullscreen) {
    document.webkitExitFullscreen();
  } else if (document.mozCancelFullScreen) {
    document.mozCancelFullScreen();
  } else if (document.msExitFullscreen) {
    document.msExitFullscreen();
  }
}

function handleFullscreenChange() {
  const isFullscreen = !!(document.fullscreenElement || document.webkitFullscreenElement || document.mozFullScreenElement || document.msFullscreenElement);
  if (fullscreenBtn) {
    fullscreenBtn.title = isFullscreen ? '退出全屏 (Esc)' : '全屏 (F11)';
    fullscreenBtn.style.opacity = isFullscreen ? '0.8' : '1';
  }
}

document.addEventListener('fullscreenchange', handleFullscreenChange);
document.addEventListener('webkitfullscreenchange', handleFullscreenChange);
document.addEventListener('mozfullscreenchange', handleFullscreenChange);
document.addEventListener('MSFullscreenChange', handleFullscreenChange);

document.addEventListener('keydown', (e) => {
  if (e.key === 'F11') {
    e.preventDefault();
    toggleFullscreen();
  }
});

// 检测 HLS 流是否可用
async function checkHlsAvailable() {
  try {
    const response = await fetch(hlsUrl, { method: 'HEAD' });
    return response.ok;
  } catch (error) {
    return false;
  }
}

// 启动 HLS 播放
function startHlsPlayback() {
  if (hls) {
    hls.destroy();
    hls = null;
  }

  if (Hls.isSupported()) {
    hls = new Hls({
      enableWorker: true,
      lowLatencyMode: true,
      backBufferLength: 90
    });
    
    hls.loadSource(hlsUrl);
    hls.attachMedia(player);
    
    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      console.log('HLS 流加载成功');
      statusMessage.style.display = 'none';
      player.play().catch(err => console.error('播放失败:', err));
    });
    
    hls.on(Hls.Events.ERROR, (event, data) => {
      console.error('HLS 错误:', data);
      if (data.fatal) {
        switch (data.type) {
          case Hls.ErrorTypes.NETWORK_ERROR:
            console.log('网络错误，尝试恢复...');
            hls.startLoad();
            break;
          case Hls.ErrorTypes.MEDIA_ERROR:
            console.log('媒体错误，尝试恢复...');
            hls.recoverMediaError();
            break;
          default:
            console.log('致命错误，无法恢复');
            hls.destroy();
            hls = null;
            showWaitingStatus();
            break;
        }
      }
    });
  } else if (player.canPlayType('application/vnd.apple.mpegurl')) {
    // Safari 原生支持 HLS
    player.src = hlsUrl;
    player.addEventListener('loadedmetadata', () => {
      statusMessage.style.display = 'none';
    });
    player.addEventListener('error', () => {
      showWaitingStatus();
    });
  } else {
    alert('您的浏览器不支持 HLS 播放');
  }
}

// 显示等待状态
function showWaitingStatus() {
  statusMessage.style.display = 'block';
  statusTitle.textContent = '等待客户端推流...';
  statusText.textContent = '正在检测 HLS 流...';
}

// 轮询检测 HLS 流
async function pollHlsStream() {
  if (retryCount >= MAX_RETRIES) {
    statusTitle.textContent = '等待超时';
    statusText.textContent = '客户端可能未启动推流，请检查客户端状态';
    return;
  }

  const available = await checkHlsAvailable();
  if (available) {
    console.log('检测到 HLS 流，开始播放');
    if (checkInterval) {
      clearInterval(checkInterval);
      checkInterval = null;
    }
    startHlsPlayback();
    retryCount = 0;
  } else {
    retryCount++;
    statusText.textContent = `正在检测 HLS 流... (${retryCount}/${MAX_RETRIES})`;
    console.log(`HLS 流不可用，继续检测... (${retryCount}/${MAX_RETRIES})`);
  }
}

// 初始化
showWaitingStatus();
pollHlsStream();
checkInterval = setInterval(pollHlsStream, 1000); // 每秒检测一次

// 页面卸载时清理
window.addEventListener('beforeunload', () => {
  if (hls) {
    hls.destroy();
    hls = null;
  }
  if (checkInterval) {
    clearInterval(checkInterval);
  }
});
  </script>
</body>
</html>"""


def check_hls_exists(ip: str) -> bool:
    """
    检查指定 IP 的 HLS 文件是否存在
    
    Args:
        ip: 客户端 IP 地址
        
    Returns:
        HLS 文件是否存在
    """
    safe_ip = ip.replace("/", "_")
    hls_path = os.path.join(HLS_ROOT, safe_ip, "index.m3u8")
    return os.path.exists(hls_path)


def get_hls_url(ip: str) -> Optional[str]:
    """
    获取指定 IP 的 HLS 播放地址
    
    Args:
        ip: 客户端 IP 地址
        
    Returns:
        HLS URL 或 None
    """
    if check_hls_exists(ip):
        safe_ip = ip.replace("/", "_")
        return f"/hls/{safe_ip}/index.m3u8"
    return None


async def handle_preview(request: Request) -> Response:
    """
    处理预览请求
    GET /preview?ip=xxx
    返回 HLS 播放页面
    """
    ip = request.query.get("ip", "-")
    
    # 总是返回播放页面，页面内会轮询检测 HLS 流
    return web.Response(text=HLS_PREVIEW_HTML, content_type="text/html")


async def handle_hls_file(request: Request) -> Response:
    """
    提供 HLS 文件访问
    GET /hls/{ip}/index.m3u8
    GET /hls/{ip}/{segment}.ts
    """
    ip = request.match_info.get("ip", "")
    filename = request.match_info.get("filename", "index.m3u8")
    
    safe_ip = ip.replace("/", "_")
    file_path = os.path.join(HLS_ROOT, safe_ip, filename)
    
    if not os.path.exists(file_path):
        return web.Response(status=404, text="File not found")
    
    # 根据文件扩展名设置 Content-Type
    if filename.endswith(".m3u8"):
        content_type = "application/vnd.apple.mpegurl"
    elif filename.endswith(".ts"):
        content_type = "video/mp2t"
    else:
        content_type = "application/octet-stream"
    
    return web.FileResponse(file_path, headers={"Content-Type": content_type})


async def handle_status(request: Request) -> Response:
    """
    获取服务器状态（用于调试）
    GET /status
    """
    import glob
    
    # 扫描所有 HLS 流
    active_streams = []
    if os.path.exists(HLS_ROOT):
        for ip_dir in os.listdir(HLS_ROOT):
            ip_path = os.path.join(HLS_ROOT, ip_dir)
            if os.path.isdir(ip_path):
                m3u8_path = os.path.join(ip_path, "index.m3u8")
                if os.path.exists(m3u8_path):
                    # 获取最新的 .ts 文件修改时间，判断流是否活跃
                    ts_files = glob.glob(os.path.join(ip_path, "*.ts"))
                    latest_ts_time = 0
                    if ts_files:
                        latest_ts_time = max(os.path.getmtime(f) for f in ts_files)
                    active_streams.append({
                        "ip": ip_dir.replace("_", "/"),
                        "has_playlist": True,
                        "latest_segment_time": latest_ts_time
                    })
    
    return web.json_response({
        "hls_root": HLS_ROOT,
        "active_streams_count": len(active_streams),
        "active_streams": active_streams
    })


def create_app() -> web.Application:
    """创建 aiohttp 应用"""
    app = web.Application()
    
    # 路由
    app.router.add_get('/preview', handle_preview)
    app.router.add_get('/status', handle_status)
    app.router.add_get('/hls/{ip}/{filename:.*}', handle_hls_file)
    
    # CORS 支持
    @web.middleware
    async def cors_middleware(request: Request, handler):
        if request.method == 'OPTIONS':
            return web.Response(headers={
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type',
            })
        response = await handler(request)
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response
    
    app.middlewares.append(cors_middleware)
    
    return app


async def init_app() -> web.Application:
    """初始化应用"""
    return create_app()


def start_webrtc_server() -> None:
    """
    启动 HLS 预览服务器（保持函数名不变，兼容 backend.py）
    """
    main()


def main() -> None:
    """启动 HLS 预览服务器"""
    print(f"🚀 启动 HLS 预览服务器 (port {SERVER_PORT})")
    print(f"📁 HLS 根目录: {HLS_ROOT}")
    
    # 确保 HLS 根目录存在
    os.makedirs(HLS_ROOT, exist_ok=True)
    
    app = init_app()
    web.run_app(app, host="0.0.0.0", port=SERVER_PORT)


if __name__ == "__main__":
    main()