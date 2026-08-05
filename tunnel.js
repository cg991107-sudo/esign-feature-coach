const lt = require('localtunnel');
const fs = require('fs');

(async () => {
  try {
    const tunnel = await lt({ port: 5055 });
    const url = tunnel.url;
    console.log('TUNNEL_URL:' + url);
    fs.writeFileSync('/tmp/tunnel_url.txt', url);
    tunnel.on('close', () => {
      fs.writeFileSync('/tmp/tunnel_url.txt', 'CLOSED');
      console.log('Tunnel closed');
    });
    // 保持进程运行
    setInterval(() => {}, 1000);
  } catch (e) {
    console.error('Error:', e.message);
    fs.writeFileSync('/tmp/tunnel_url.txt', 'ERROR:' + e.message);
    process.exit(1);
  }
})();
