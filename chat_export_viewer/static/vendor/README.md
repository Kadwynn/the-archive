# Vendored Markdown libraries

These browser builds are kept locally so formatted chat reading works without a CDN.
They are unmodified files from the official npm packages, downloaded on 2026-10-25.
The complete package tarballs were checked against npm's SHA-512 integrity value before extracting only the named build and license files. `manifest.json` records the pinned package URLs, integrity values, and extracted-build SHA-256 hashes.

- **Marked 18.0.14**: `marked.umd.js`, MIT license in `marked-LICENSE.txt`.
  Official project: https://github.com/markedjs/marked
  Release: https://github.com/markedjs/marked/releases/tag/v18.0.14
  Renderer documentation: https://marked.js.org/using_pro
- **DOMPurify 3.4.16**: `purify.min.js`, license text in `dompurify-LICENSE.txt` (Apache-2.0 OR MPL-2.0).
  Official project: https://github.com/cure53/DOMPurify
  Release: https://github.com/cure53/DOMPurify/releases/tag/3.4.16

Load `marked.umd.js`, then `purify.min.js`, then the viewer's `markdown.js` before `app.js`. The viewer escapes raw HTML, sanitizes Markdown output, limits links to absolute HTTP(S)/email destinations, and turns image syntax into explicit links instead of loading remote media.
