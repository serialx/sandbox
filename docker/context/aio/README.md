# `aio` CLI — recovered & re-authored into a buildable package

`aio` (the `aio-sandbox` CLI, v0.3.12) is the single `#!/usr/bin/env node`
script at `/usr/local/bin/aio` in the live image. It was shipped as an
**esbuild bundle built without minification**, which made its own source
recoverable. This directory turns that recovery into a real, buildable npm
package whose `esbuild` output is **behaviourally identical** to the original.

## Layout

```
context/aio/
├── package.json        # buildable package (commander, chalk, cosmiconfig, typescript)
├── package-lock.json   # pinned dependency tree
├── build.mjs           # esbuild build -> dist/aio.js (ESM, shebang banner)
├── src/**.js           # ★ the re-authored, buildable source (17 ESM modules)
├── recovered-src/      # provenance: the raw module slices cut from the bundle
└── _generate.mjs       # one-shot tool that produced src/ from recovered-src/
```

`npm ci && npm run build` produces `dist/aio.js`, a single self-contained node
script. The Dockerfile's `aio-build` stage does exactly this and installs the
result to `/usr/local/bin/aio`.

## How it was recovered

1. **Slice** — the un-minified bundle keeps esbuild's `// <path>` banners, so
   aio's own 17 `src/**` modules were cut out verbatim into `recovered-src/`.
2. **Re-author** (`_generate.mjs`) — esbuild had merged every module into one
   shared scope (stripping `import`/`export`, renaming on collision). The
   generator rebuilds the module graph: it maps every top-level symbol to its
   owning module, then re-adds `export`s and the cross-module + vendor `import`s.
   The dependency graph is a clean DAG.
3. **Hand-fix two files**:
   * `config.js` — restore `import { cosmiconfig } from "cosmiconfig"` in place
     of esbuild's `__toESM(require_dist())` interop.
   * `index.js` — drop the `if (false) { … }` blocks where the upstream build
     dead-code-eliminated the non-sandbox commands and the aipaas remote
     provider (`new (void 0)(…)`), which left dangling references.
4. **Build** — `esbuild --bundle --format=esm` (ESM is required: `index.js` uses
   top-level `await loadConfig()`).

## Fidelity

* The vendor surface is tiny and confirmed from the bundle: **commander**
  (`Command`), **chalk** (`source_default` — the bundle literally has
  `var source_default = chalk`), **cosmiconfig** (config search), and
  **typescript** (pulled in statically by cosmiconfig's TS loader — this is what
  makes the bundle ~10 MB). Everything else is `node:*` builtins or Node globals
  (`fetch`/`FormData`/`Blob`).
* Built size **10,328,279 B** vs. the original **10,312,120 B** (+0.16 %).
* Verified against the original `/usr/local/bin/aio`: identical `--version`,
  identical top-level `--help`, identical `--help` for all 5 commands and all
  ~45 subcommands, identical output for read-only commands (`mcp list`,
  `skills list`, `sandbox status`) against the live server, and identical
  unknown-command error handling.

## What is *not* recovered

The original **TypeScript** — the bundle carries no sourcemap, so `src/**` is
esbuild's **transpiled JS** (the `.js` files use the original `.ts` module
paths). It compiles and runs identically; it just isn't the authored TS. Names
that esbuild renamed on collision are kept as-is (e.g. `program2`,
`source_default`, `homedir2`) so the result stays verifiably equivalent rather
than prettified.
