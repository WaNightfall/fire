const http = require('http');
const fs = require('fs');
const path = require('path');

const DIR = path.join(__dirname, 'output');
const PORT = 8765;

http.createServer((req, res) => {
  const filePath = path.join(DIR, req.url === '/' ? 'dashboard.html' : req.url);
  fs.readFile(filePath, (err, data) => {
    if (err) { res.writeHead(404); res.end('Not found'); return; }
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end(data);
  });
}).listen(PORT, () => {
  console.log('Kilauea Dashboard server running at http://localhost:' + PORT);
});
