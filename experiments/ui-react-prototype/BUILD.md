# Devices page prototype (experiment)

Not part of the product and not shipped in the wheel. It shows what one page costs when it is a Preact component. It reuses the dashboard's real CSS files, so the page looks the same as the vanilla one.

Build on the Mac (Node 22 or newer, needs the network once for `npm install`):

```sh
cd experiments/ui-react-prototype
npm install
npm run sizes          # builds dist/preact.html and dist/react.html and prints the sizes
open dist/preact.html  # one self-contained file: CSS and JS inline, no network
```

Measured on 2026-10-09 (esbuild 0.28, preact 10.29, react 19.3, minified, whole page script including the app):

| Build | Raw | Gzip level 9 | Brotli |
| --- | --- | --- | --- |
| Preact | 15.5 KB | 6.4 KB | 5.8 KB |
| React 19 | 224.4 KB | 69.7 KB | 60.1 KB |

For comparison `dashboard.js` is 423 KB raw today and covers all nine pages.

To ship a build the output would be committed as a static file next to the other dashboard assets and added to `dashboard.py`, so the wheel and the Pi never need Node. Inline event handlers and `style=` attributes are not used, because the Content-Security-Policy only allows hashed inline script and style blocks.
