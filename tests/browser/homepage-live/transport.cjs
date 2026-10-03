"use strict";
const { execFileSync } = require("node:child_process");
const { createHash, X509Certificate } = require("node:crypto");
const fs = require("node:fs/promises");
const https = require("node:https");
const os = require("node:os");
const path = require("node:path");

exports.withDeadline = async (operation, milliseconds) => {
  let timer;
  try {
    return await Promise.race([operation, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error("Fixture cleanup deadline exceeded")), milliseconds);
    })]);
  } finally { clearTimeout(timer); }
};

exports.startDocumentTransport = async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "homepage-live-https-"));
  let server;
  const sockets = new Set();
  try {
    const key = path.join(directory, "server.key");
    const certificate = path.join(directory, "server.crt");
    execFileSync("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes",
      "-keyout", key, "-out", certificate, "-days", "1", "-subj", "/CN=example.org",
      "-addext", "subjectAltName=DNS:example.org"],
      { timeout: 10000, maxBuffer: 65536, stdio: "pipe" });
    const cert = await fs.readFile(certificate);
    const publicKey = new X509Certificate(cert).publicKey.export({ type: "spki", format: "der" });
    const fingerprint = createHash("sha256").update(publicKey).digest("base64");
    const reads = [];
    server = https.createServer({ key: await fs.readFile(key), cert, handshakeTimeout: 3000, maxHeaderSize: 8192 },
      (request, response) => {
        if (request.method !== "GET" || request.headers.host !== "example.org" ||
            !["/", "/article"].includes(request.url) || reads.length >= 64) {
          response.writeHead(404, { "Connection": "close" });
          response.end();
          return;
        }
        reads.push(request.url);
        response.writeHead(200, { "Content-Type": "text/html", "Cache-Control": "no-store" });
        response.end("<h1>Controlled HTTPS test page</h1>");
      });
    server.on("connection", socket => {
      sockets.add(socket);
      socket.once("close", () => sockets.delete(socket));
    });
    server.maxConnections = 8;
    server.maxRequestsPerSocket = 8;
    server.requestTimeout = 3000;
    server.headersTimeout = 3000;
    server.keepAliveTimeout = 1000;
    server.setTimeout(3000, socket => socket.destroy());
    await new Promise((resolve, reject) => {
      server.once("error", reject);
      server.listen(0, "127.0.0.1", resolve);
    });
    return {
      reads,
      args: ["--host-resolver-rules=MAP example.org 127.0.0.1:" + server.address().port,
        "--ignore-certificate-errors-spki-list=" + fingerprint, "--no-proxy-server"],
      close: async () => {
        const closing = new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
        sockets.forEach(socket => socket.destroy());
        server.closeAllConnections();
        try { await exports.withDeadline(closing, 3000); } finally { await fs.rm(directory, { recursive: true, force: true }); }
      }
    };
  } catch (error) {
    sockets.forEach(socket => socket.destroy());
    if (server) { server.closeAllConnections(); server.close(); }
    await fs.rm(directory, { recursive: true, force: true });
    throw error;
  }
};
