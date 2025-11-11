// 自动配置：支持远程部署，自动获取正确的服务器地址和端口
(function() {
  const protocol = window.location.protocol;
  const hostname = window.location.hostname;
  const port = window.location.port;
  
  // 默认端口配置
  const DEFAULT_API_PORT = '5001';
  const DEFAULT_WEBRTC_PORT = '5002';
  
  // 构建URL的辅助函数
  const buildUrl = (targetPort, path = '') => {
    // 如果当前页面有端口，使用当前主机名和目标端口
    if (port) {
      return `${protocol}//${hostname}:${targetPort}${path}`;
    }
    // 本地开发环境，添加端口
    if (hostname === 'localhost' || hostname === '127.0.0.1') {
      return `${protocol}//${hostname}:${targetPort}${path}`;
    }
    // 生产环境：非标准端口必须包含端口号
    if (targetPort !== '80' && targetPort !== '443') {
      return `${protocol}//${hostname}:${targetPort}${path}`;
    }
    // 标准端口，根据协议决定
    if (targetPort === '80' && protocol === 'http:') {
      return `http://${hostname}${path}`;
    } else if (targetPort === '443' && protocol === 'https:') {
      return `https://${hostname}${path}`;
    } else {
      return `${protocol}//${hostname}:${targetPort}${path}`;
    }
  };
  
  // 初始化默认配置（从当前页面推断）
  const defaultConfig = {
    apiBase: buildUrl(DEFAULT_API_PORT, '/api'),
    webrtcBase: buildUrl(DEFAULT_WEBRTC_PORT, ''),
    uploadsBase: buildUrl(DEFAULT_API_PORT, '/uploads')
  };
  
  // 如果已经手动配置，保留手动配置
  window.APP_CONFIG = window.APP_CONFIG || {};
  if (!window.APP_CONFIG.apiBase) window.APP_CONFIG.apiBase = defaultConfig.apiBase;
  if (!window.APP_CONFIG.webrtcBase) window.APP_CONFIG.webrtcBase = defaultConfig.webrtcBase;
  if (!window.APP_CONFIG.uploadsBase) window.APP_CONFIG.uploadsBase = defaultConfig.uploadsBase;
  
  // 从后端API获取准确配置（优先使用后端返回的配置）
  // 构建API配置URL（使用当前页面的origin + /api/config）
  const configUrl = `${window.location.origin}/api/config`;
  
  // 使用fetch获取配置（异步，但如果失败会使用默认配置）
  fetch(configUrl, { credentials: 'include' })
    .then(res => {
      if (res.ok) {
        return res.json();
      }
      throw new Error(`HTTP ${res.status}`);
    })
    .then(config => {
      // 更新配置
      if (config.apiBase) {
        window.APP_CONFIG.apiBase = config.apiBase;
        console.log('✅ 更新 apiBase:', config.apiBase);
      }
      if (config.webrtcBase) {
        window.APP_CONFIG.webrtcBase = config.webrtcBase;
        console.log('✅ 更新 webrtcBase:', config.webrtcBase);
      }
      if (config.uploadsBase) {
        window.APP_CONFIG.uploadsBase = config.uploadsBase;
        console.log('✅ 更新 uploadsBase:', config.uploadsBase);
      }
      console.log('✅ 最终配置:', window.APP_CONFIG);
    })
    .catch(error => {
      console.warn('⚠️ 无法从服务器获取配置，使用默认配置:', error.message);
      console.log('📋 当前配置:', window.APP_CONFIG);
    });
})();


