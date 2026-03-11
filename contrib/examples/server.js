#!/usr/bin/env node
/**
 * Timeline dev server
 * - Serves static files from contrib/examples/
 * - POST /api/save/:filename  →  writes JSON back to disk
 *
 * Usage:  node server.js        (default port 8888)
 *         node server.js 3000   (custom port)
 */
const http = require('http');
const fs   = require('fs');
const path = require('path');
const url  = require('url');

const PORT = parseInt(process.argv[2]) || 8888;
const DIR  = __dirname;

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js':   'text/javascript; charset=utf-8',
  '.css':  'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png':  'image/png',
  '.jpg':  'image/jpeg',
  '.svg':  'image/svg+xml',
  '.ico':  'image/x-icon',
};

const server = http.createServer((req, res) => {
  const parsed = url.parse(req.url, true);
  const pathname = decodeURIComponent(parsed.pathname);

  // ── POST /api/save/:filename ──────────────────────────────
  if (req.method === 'POST' && pathname.startsWith('/api/save/')) {
    const filename = path.basename(pathname.slice('/api/save/'.length));

    // Security: only allow simple *.json filenames, no path traversal
    if (!filename.match(/^[\w.-]+\.json$/) || filename.includes('..')) {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ error: 'Invalid filename' }));
    }

    const filepath = path.join(DIR, filename);
    if (!fs.existsSync(filepath)) {
      res.writeHead(404, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ error: 'File not found: ' + filename }));
    }

    let body = '';
    req.on('data', chunk => { body += chunk; });
    req.on('end', () => {
      try {
        const data = JSON.parse(body);
        fs.writeFileSync(filepath, JSON.stringify(data, null, 2));
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true }));
      } catch (e) {
        res.writeHead(500, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  // ── Static file serving ───────────────────────────────────
  let filePath = path.join(DIR, pathname === '/' ? 'index.html' : pathname);

  // Prevent escaping the serve directory
  if (!filePath.startsWith(DIR)) {
    res.writeHead(403); return res.end('Forbidden');
  }

  const ext = path.extname(filePath).toLowerCase();
  fs.readFile(filePath, (err, data) => {
    if (err) { res.writeHead(404); return res.end('Not found: ' + pathname); }
    res.writeHead(200, { 'Content-Type': MIME[ext] || 'application/octet-stream' });
    res.end(data);
  });
});

server.listen(PORT, '0.0.0.0', () => {
  console.log(`\n  Timeline server started`);
  console.log(`  → http://localhost:${PORT}/\n`);
});
