import { createRequire } from 'node:module';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { dirname, resolve, extname } from 'node:path';
import { mkdir, readFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { spawn } from 'node:child_process';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const require = createRequire(resolve(root, 'apps/extension/package.json'));
const { build } = await import(pathToFileURL(require.resolve('vite')).href);
const outDir = resolve(root, 'tmp/store-screenshots');
await build({
  configFile: false, root: resolve(root, 'scripts/store-screenshots'),
  resolve: { alias: { '@': resolve(root, 'apps/extension'), react: resolve(root, 'apps/extension/node_modules/react'), 'react-dom': resolve(root, 'apps/extension/node_modules/react-dom') } },
  oxc: { jsx: { runtime: 'automatic' } },
  build: { outDir, emptyOutDir: false },
});
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' };
const server = createServer(async (req, res) => {
  try {
    const pathname = new URL(req.url, 'http://localhost').pathname;
    const target = resolve(outDir, '.' + (pathname === '/' ? '/index.html' : pathname));
    if (!target.startsWith(outDir + '/') && !target.startsWith(outDir + '\\')) throw new Error('Invalid path');
    res.setHeader('Content-Type', types[extname(target)] ?? 'application/octet-stream');
    res.end(await readFile(target));
  } catch { res.writeHead(404); res.end(); }
});
await new Promise((done) => server.listen(0, '127.0.0.1', done));
const port = server.address().port;
const output = resolve(root, 'docs/store-assets/screenshots');
await mkdir(output, { recursive: true });
try {
  for (const [index, view] of ['record', 'drafts', 'detail', 'analytics', 'settings'].entries()) {
    const profile = resolve(root, `tmp/store-screenshots/profile-${view}`);
    const destination = resolve(output, `${index + 1}-${view}-1280x800.png`);
    await new Promise((done, reject) => {
      const child = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', [
        '--headless=new', '--no-first-run', '--no-default-browser-check', '--disable-extensions',
        '--disable-background-networking', '--force-dark-mode', '--hide-scrollbars',
        '--force-device-scale-factor=1', '--window-size=1280,800', '--virtual-time-budget=6000',
        `--user-data-dir=${profile}`, `--screenshot=${destination}`, `http://127.0.0.1:${port}/?view=${view}`,
      ], { windowsHide: true, stdio: 'ignore' });
      child.on('error', reject);
      child.on('exit', (code) => code === 0 ? done() : reject(new Error(`Chrome exited ${code}`)));
    });
    console.log(destination);
  }
} finally { server.close(); }
await new Promise((done, reject) => {
  const normalize = spawn('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', resolve(root, 'scripts/store-screenshots/normalize.ps1')], { windowsHide: true, stdio: 'inherit' });
  normalize.on('error', reject);
  normalize.on('exit', (code) => code === 0 ? done() : reject(new Error('PNG format verification failed')));
});
