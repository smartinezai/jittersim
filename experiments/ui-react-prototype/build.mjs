// Build the prototype into one HTML file with inline CSS and JS, the way the real page is served.
// `node build.mjs --sizes` also builds the same page against React for a size comparison.
import { build } from "esbuild";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { gzipSync, brotliCompressSync } from "node:zlib";

const api = new URL("../../src/jittersim/api/", import.meta.url);
const css = ["dashboard.css", "workflows.css", "access.css", "phone.css", "refresh.css"]
  .map((name) => readFileSync(new URL(name, api), "utf8")).join("\n");

async function bundle(label, options) {
  const result = await build({
    entryPoints: ["src/main.jsx"], bundle: true, minify: true, write: false, format: "iife",
    target: "es2020", jsx: "automatic", legalComments: "none", ...options,
  });
  const js = result.outputFiles[0].text;
  return { label, js, raw: Buffer.byteLength(js), gzip: gzipSync(js, { level: 9 }).length, brotli: brotliCompressSync(js).length };
}

const preact = await bundle("preact", { jsxImportSource: "preact" });
mkdirSync("dist", { recursive: true });
const page = (js) => `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>JitterSim Devices prototype</title><style>${css}</style></head><body><main class="content" style="max-width:60rem;margin:auto"><div id="root"></div></main><script>${js}</script></body></html>`;
writeFileSync("dist/preact.html", page(preact.js));
const rows = [preact];

if (process.argv.includes("--sizes")) {
  const react = await bundle("react 19", { jsxImportSource: "react", alias: { preact: "react", "preact/hooks": "react" },
    plugins: [{ name: "react-dom-render", setup(b) {
      b.onLoad({ filter: /src\/main\.jsx$/ }, (args) => {
        const source = readFileSync(args.path, "utf8")
          .replace('import { render } from "preact";', 'import { createRoot } from "react-dom/client";')
          .replace('import { useState, useMemo } from "preact/hooks";', 'import { useState, useMemo } from "react";')
          .replace('render(<App />, document.getElementById("root"));', 'createRoot(document.getElementById("root")).render(<App />);')
          .replaceAll("class=", "className=");
        return { contents: source, loader: "jsx" };
      });
    } }],
    define: { "process.env.NODE_ENV": '"production"' } });
  writeFileSync("dist/react.html", page(react.js));
  rows.push(react);
}
const cssGz = gzipSync(css, { level: 9 }).length;
console.log("label        raw(B)   gzip(B)  brotli(B)");
for (const r of rows) console.log(r.label.padEnd(10), String(r.raw).padStart(9), String(r.gzip).padStart(9), String(r.brotli).padStart(10));
console.log(`css (inlined, shared with the product): raw ${Buffer.byteLength(css)} B, gzip ${cssGz} B`);
