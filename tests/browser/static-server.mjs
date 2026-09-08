// Test-only server: built public assets, GET/HEAD, loopback, no directory listing.
import http from 'node:http';
import path from 'node:path';
import {readFile, realpath, stat} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';

const root = await realpath(fileURLToPath(new URL('../../dist/', import.meta.url)));
const mime = {'.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.webp': 'image/webp', '.jpg': 'image/jpeg', '.woff2': 'font/woff2', '.ico': 'image/x-icon'};
const inside = target => target.startsWith(root + path.sep);

http.createServer(async (request, response) => {
  if (!['GET', 'HEAD'].includes(request.method)) {
    response.writeHead(405).end();
    return;
  }
  try {
    const pathname = decodeURIComponent(new URL(request.url, 'http://127.0.0.1:4173').pathname);
    const requested = path.resolve(root, `.${pathname}`);
    // Canonical /en/features and /features map to the actual generated .html files.
    for (const candidate of [requested, requested + '.html', path.join(requested, 'index.html')]) {
      if (!inside(candidate)) continue;
      try {
        const resolved = await realpath(candidate);
        if (!inside(resolved) || !(await stat(resolved)).isFile()) continue;
        response.writeHead(200, {'Content-Type': mime[path.extname(resolved)] || 'application/octet-stream', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'});
        response.end(request.method === 'HEAD' ? undefined : await readFile(resolved));
        return;
      } catch (error) {
        if (!['ENOENT', 'ENOTDIR'].includes(error.code)) throw error;
      }
    }
    response.writeHead(404).end('Not found');
  } catch {
    if (!response.headersSent) response.writeHead(400);
    response.end();
  }
}).listen(4173, '127.0.0.1');
