#!/usr/bin/env node
// canvas-proxy.mjs — Proxy reverso para OpenClaw Gateway + Dashboard
// Injeta Bearer token nas requisições HTTP e faz túnel TCP para WebSockets
//
// Roteamento:
//   /__openclaw__/*  → Gateway (porta 18789)
//   /*               → Dashboard / Control UI (porta gateway+2, ex: 18791)
//
// Uso: node scripts/canvas-proxy.mjs
//      CANVAS_PROXY_PORT=8888 CLAW_GATEWAY_PORT=18789 node scripts/canvas-proxy.mjs

import http from 'http';
import net from 'net';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const PROXY_PORT = parseInt(process.env.CANVAS_PROXY_PORT || '8888');
const TARGET_HOST = '127.0.0.1';
const GATEWAY_PORT = parseInt(process.env.CLAW_GATEWAY_PORT || '18789');

// Carrega token do env ou do quanttix.env
let TOKEN = process.env.OPENCLAW_GATEWAY_TOKEN || '';
if (!TOKEN) {
  try {
    const envFile = join(SCRIPT_DIR, 'quanttix.env');
    const content = readFileSync(envFile, 'utf8');
    const match = content.match(/OPENCLAW_GATEWAY_TOKEN="?([^"\n]+)"?/);
    if (match) TOKEN = match[1];
  } catch {}
}

if (!TOKEN) {
  console.error('[canvas-proxy] OPENCLAW_GATEWAY_TOKEN não encontrado.');
  process.exit(1);
}

const server = http.createServer((req, res) => {
  const targetPort = GATEWAY_PORT;
  const options = {
    hostname: TARGET_HOST,
    port: targetPort,
    path: req.url,
    method: req.method,
    headers: {
      ...req.headers,
      host: `${TARGET_HOST}:${targetPort}`,
      authorization: `Bearer ${TOKEN}`,
    },
  };

  const proxy = http.request(options, (proxyRes) => {
    res.writeHead(proxyRes.statusCode, proxyRes.headers);
    proxyRes.pipe(res, { end: true });
  });

  req.pipe(proxy, { end: true });

  proxy.on('error', (err) => {
    res.writeHead(502);
    res.end(`Proxy error: ${err.message}`);
  });
});

// WebSocket: túnel TCP com Bearer injetado no upgrade request
server.on('upgrade', (req, clientSocket, head) => {
  const targetSocket = net.connect(GATEWAY_PORT, TARGET_HOST, () => {
    const upgradeHeaders = {
      ...req.headers,
      host: `${TARGET_HOST}:${GATEWAY_PORT}`,
      authorization: `Bearer ${TOKEN}`,
    };
    const headerLines = Object.entries(upgradeHeaders)
      .map(([k, v]) => `${k}: ${v}`)
      .join('\r\n');
    const upgradeReq = `${req.method} ${req.url} HTTP/${req.httpVersion}\r\n${headerLines}\r\n\r\n`;
    targetSocket.write(upgradeReq);
    if (head && head.length) targetSocket.write(head);
    targetSocket.pipe(clientSocket);
    clientSocket.pipe(targetSocket);
  });

  targetSocket.on('error', () => clientSocket.destroy());
  clientSocket.on('error', () => targetSocket.destroy());
});

server.listen(PROXY_PORT, '0.0.0.0', () => {
  console.log(`[canvas-proxy] porta ${PROXY_PORT} → ${TARGET_HOST}:${GATEWAY_PORT}`);
});
