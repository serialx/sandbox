# AIO Sandbox — reconstructed Docker build

This directory reconstructs the Docker image that runs the **AIO Sandbox**
(`all-in-one-sandbox`). The original build recipe was lost, so the `Dockerfile`
here was **reverse-engineered from a live container** of
`ghcr.io/agent-infra/sandbox:latest`.

> The public `ghcr.io/agent-infra/sandbox` image is only a multi-arch *mirror*
> of an upstream prebuilt image
> (`enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox`,
> see `.github/workflows/push-to-ghcr.yml`). The actual Dockerfile was never
> published — this is a faithful reproduction, not a copy.

## What the image is

An "all-in-one" agent sandbox: a single container exposing (behind an nginx
gateway on `:8080`) a code-execution server, JupyterLab, code-server (VS Code),
a full VNC desktop with Chrome, an MCP hub, and several MCP servers.

| Service | Port | Source |
|---|---|---|
| nginx gateway / dashboard | 8080 | apt `nginx` + recovered `/opt/gem/nginx*.conf` |
| sandbox `python-server` — shell/file/jupyter APIs **and the MCP hub** (`/mcp`) | 8091 | **recovered** `python-server` wheel (py3.12) |
| JupyterLab | 8888 | pip (py3.12) |
| Node.js REPL (code execution) | 8092 | recovered `repl-servers/nodejs` (node 22) |
| code-server (VS Code) | 8200 | code-server 4.104.0 release |
| MCP server: browser | 8100 | npm `@agent-infra/mcp-server-browser` |
| MCP servers: markitdown, chrome-devtools | on-demand | `markitdown` (pip) / `chrome-devtools-mcp` (npm) — spawned by the hub on demand, **not** fixed-port services |
| TigerVNC `Xvnc` | 5900 | apt `tigervnc-standalone-server` |
| VNC websocket bridge (`websocat`) | 6080 | websocat 1.13.0 + noVNC 1.4.0 |
| Chrome remote debugging (CDP) | 9222 | google-chrome-stable (amd64) / Playwright Chromium (arm64) |

Everything is orchestrated by **supervisord** (PID 1 is
`/opt/application/run.sh`, which derives all runtime config and then
`exec`s `supervisord`). supervisord runs as root and drops to user `gem`
(uid 1000) per program.

## Reconstruction strategy

Components fall into three buckets:

1. **Public upstreams** — installed from their normal sources, pinned to the
   versions observed in the live image:
   * `ubuntu:22.04` base, ~160 apt packages (fonts, X/VNC, fcitx5 IME, media
     libs, chrome runtime libs, build toolchain).
   * Python **3.11.13** + **3.12.11** via `uv` (python-build-standalone — the
     same builds the original used).
   * Node **20.20.2 / 22.22.2 / 24.15.0** via `fnm`; npm globals `bun@1.3.3`,
     `@agent-infra/mcp-server-browser@1.2.29`, `agent-browser@0.22.3`,
     `chrome-devtools-mcp@0.9.0`, `yarn`.
   * `uv 0.8.9`, `code-server 4.104.0`, `noVNC 1.4.0`, `websocat 1.13.0`,
     `gost 3.0.0-rc10` (same versions on both arches — per-arch release assets).
     Browser: `google-chrome-stable 139.0.7258.127` on amd64; Playwright
     Chromium (build 1194) on arm64.
   * Three pip dependency sets, reproduced verbatim from the live `pip freeze`
     (`requirements/`):
     * `system-python3.10.txt` (150 pkgs) — user-facing tooling (manim,
       weasyprint, opencv, yt-dlp, …) **and supervisord** (pinned git commit).
     * `python3.11.txt` (38 pkgs) — a lightweight matplotlib/ipykernel kernel.
     * `python3.12.txt` (240 pkgs) — the sandbox-server runtime, JupyterLab,
       markitdown, MCP. (`python3.12.pypi.txt` is the same list with the two
       in-house wheels removed; they are built and installed separately.)

2. **Recovered from the live image, rebuilt from that source** (`context/`):
   * `context/python-server/` — the `python-server` package (`app` + `vendors`,
     114 modules), copied out of site-packages with a `pyproject.toml`
     reconstructed from its `dist-info`. Built into the same
     `python_server-0.1.0` wheel and installed into py3.12.
   * `context/browser-sdk/` — its in-house dependency `browser-sdk` (Playwright
     wrapper, incl. JS helper assets), same treatment.
   * `context/repl-servers/nodejs/` — the in-house Node.js REPL server
     (`fastify` + `@swc/core`); its source is plain JS, deps installed via npm.
   * `context/aio/` — the `aio` CLI (`aio-sandbox` v0.3.12). The original was an
     un-minified esbuild bundle, so its 17 own `src/**` modules were sliced out
     and **re-authored into a buildable npm package** (re-added `import`/`export`
     graph + `package.json`). The Dockerfile's `aio-build` stage runs `esbuild`
     to produce a single-file CLI that is **behaviourally identical** to the
     original (verified: same `--version`, `--help`, every subcommand help, and
     read-only command output; built size within 0.16 %). See
     [`context/aio/README.md`](context/aio/README.md).
   * `context/rootfs/opt/gem` + `/opt/application` — the orchestration layer
     (run.sh, supervisord/nginx configs, the browser/jupyter/code-server
     launchers). These *are* the source (shell + config) and are copied
     verbatim, then re-`COPY`ed into the image.

3. **Identified from the live image, built from public source**:
   * `ve` (`linux_<arch>_ve`) — the recovered binary's **Go buildinfo** pins it
     exactly to `github.com/volcengine/volcengine-cli` (deps cobra v1.6.1,
     pflag v1.0.5, uuid, volc-sdk-golang, volcengine-go-sdk) — the public,
     Apache-2.0 **Volcengine CLI**. The `ve-build` Go stage builds it from that
     public source (`go install …/volcengine-cli@v1.0.43`, CGO-free static with
     `GOARCH=$TARGETARCH`, as goreleaser does) → `/usr/local/bin/linux_${TARGETARCH}_ve`
     (`linux_amd64_ve` / `linux_arm64_ve`). Only the tiny platform-dispatch `ve`
     wrapper (shell, dispatches by `uname -m`) comes from `rootfs`. Off the boot
     path — a Volcengine cloud CLI used only with IAM credentials.

With `ve` rebuilt from public source, **no in-house compiled binary is shipped
verbatim** — every component is installed from upstream or rebuilt from source.

The custom Chrome at `/opt/browser` in the original is simply a *flattened
`google-chrome-stable` .deb* (per `/opt/gem/browser-ctl.sh`), so on **amd64** we
reproduce exactly that — i.e. **regular Chrome**, no proprietary build. On
**arm64** (Google ships no Chrome for arm64 Linux) we use **Playwright's
Chromium** — the same fallback `browser-ctl.sh` documents. Either way the result
is a `/opt/browser/chrome` binary driven over CDP (see *Fidelity notes*).

## Build & run

```bash
# build context root is this docker/ directory
docker build -t aio-sandbox -f docker/Dockerfile docker/

# or via the repo's compose file (point image: at your local tag)
docker run --rm -p 8080:8080 --shm-size=2g \
  --security-opt seccomp=unconfined aio-sandbox
# open http://localhost:8080
```

> **Multi-arch (amd64 + arm64).** BuildKit selects your host architecture
> automatically; pin explicitly with `--platform linux/amd64` or
> `--platform linux/arm64` if needed.

## Build & runtime fixes (verified)

The reconstructed `Dockerfile` was built (both `--platform linux/amd64` and
`linux/arm64`) and run, and its runtime environment was cross-checked against a
live `env` dump from the upstream container. The fixes below were applied so the
image builds clean and boots every service:

**Build**
* `fnm` step: `mkdir -p /opt/nodejs` before creating the version symlinks.
* `npm -g` and the Node REPL `npm install`: put node 22's bin on `PATH` for the
  `RUN` (the `/usr/local/bin/node` symlinks are wired later; `npm`'s shebang is
  `#!/usr/bin/env node`).
* pip into the uv-managed `/opt/python3.11` & `/opt/python3.12` and the in-house
  wheels: add `--break-system-packages` (python-build-standalone ships a PEP-668
  `EXTERNALLY-MANAGED` marker; the apt system python does not).

**Runtime ENV** — recovered/validated from the live container's `env`
(supervisord aborts on any unset `%(ENV_*)s`):
* boot-critical, previously missing: `LOG_DIR=/var/log/gem`, `DISPLAY=:99.0`,
  `DISPLAY_DEPTH=24`, `USER_UID`/`USER_GID=1000`.
* `WAIT_PORTS=8091` (the upstream value) — **not** `8079`: nothing binds 8079
  (the MCP hub is served by `python-server` on 8091), so `8079,8091` made
  `nginx-wait.sh` block the gateway forever.
* browser: `BROWSER_EXECUTABLE_PATH=/usr/local/bin/browser`,
  `BROWSER_NO_SANDBOX=--no-sandbox`, and the full upstream `BROWSER_COMMANDLINE_ARGS`
  (incl. `--remote-debugging-port=9222 --remote-allow-origins=*`); without these
  `start-browser.sh` `exec`s an empty path and Chrome never opens CDP.
* `/etc/profile.d/nodejs.sh` symlink + an fnm `default` alias so `node` resolves
  in shells (run.sh sources the profile script under `set -e`).
* the remaining ENV mirrors the live image (CJK IME on, code-exec version vars,
  `MAX_SHELL_SESSIONS`, waits, etc.).

**Front-end UI assets** — recovered from the live image; nginx serves them at
`/static/sandbox/` (root `/var/www/app`), a tree the reconstruction's rootfs was
missing entirely:
* `context/rootfs/var/www/app/static/sandbox/` — `agent-infra-browser-ui@0.2.2`
  (the `/browser-ui` CDP client), `swagger-ui-dist@5` (`/v1/docs`),
  `xterm@5.3.0` + `xterm-addon-fit` (`/terminal`), `clipboard`, `favicon.png`.
  Without it those pages load but their JS 404s — e.g. `/browser-ui` shows
  "Reconnecting…" forever.
* noVNC ships `vnc.html` but no `index.html`; the noVNC step now copies
  `vnc.html` → `index.html` so the documented `/vnc/index.html?autoconnect=true`
  URL (and nginx's `index index.html vnc.html;`) work.

**Verified working** — core APIs via the gateway (`:8080`) and the Python SDK:
`GET /v1/sandbox`, `POST /v1/shell/exec`, `/v1/file/{write,read}`,
`/v1/nodejs/execute`; the UIs `/` (dashboard), `/v1/docs` (Swagger), `/terminal`,
`/browser-ui`, `/vnc/index.html`, JupyterLab and code-server; the full
supervisord program set; and Chrome + CDP (`:9222`). On **native hardware
(amd64 and arm64)**, additionally `POST /v1/jupyter/execute`, the `/v1/browser/*`
API (navigate + real-PNG screenshot) and `/mcp` (see the caveat below).

> **amd64-emulation caveat.** Under amd64-on-arm64 emulation (e.g. Rosetta on
> Apple Silicon) the heavily-threaded async paths — `POST /v1/jupyter/execute`
> (pyzmq kernels), the `/v1/browser/*` API (playwright/CDP) and `/mcp` —
> deadlock the python-server event loop (a glibc/CPython threading deadlock that
> freezes the whole loop). This is an **emulation artifact, not a build defect**:
> the same calls return `200` on native amd64 — **confirmed by running this
> rebuilt image on an amd64 k8s node**, where `jupyter/execute`, browser
> navigate + screenshot (real PNG), and the `/mcp` `initialize` handshake all
> succeed. Run the image on a host that matches its architecture — the **arm64
> image runs all of these natively** (no emulation), and amd64 runs them on
> amd64 hardware; only amd64-under-emulation hits the deadlock.

## Fidelity notes & caveats

* **Not byte-identical.** This rebuilds the image from sources rather than
  copying its layers; pinned versions match, but apt packages resolve to the
  current jammy point-releases and pip wheels rebuild from sdists where needed.
* **Not built inside the recovery sandbox** (the k8s pod has no Docker daemon).
  The novel/risky pieces were validated independently instead:
  * both in-house wheels build cleanly and contain their JS/shell data assets;
  * the re-authored `aio` package builds with `esbuild` and is byte-/behaviour-
    verified against the original bundle (see `context/aio/README.md`);
  * `ve` is identified from its Go buildinfo as `volcengine-cli`, and
    `volcengine-cli@v1.0.43` resolves on the Go module proxy (so the `ve-build`
    stage's `go install` will fetch it);
  * `uv` resolves the exact `3.11.13` / `3.12.11` interpreters;
  * every pinned release URL (code-server, noVNC, websocat, gost, fnm, uv,
    chrome) returns HTTP 200.
  The image has since been built and run end-to-end (see **Build & runtime
  fixes** above for the fixes applied and what was verified).
* **Multi-arch (amd64 + arm64).** Every arch-specific asset is selected from
  BuildKit's `TARGETARCH`: the `ve` Go build (`GOARCH` → `linux_<arch>_ve`), the
  uv-python dir (`x86_64`/`aarch64`), and the fnm / code-server / websocat / gost
  release assets. The one real difference is the **browser** — amd64 uses Google
  Chrome (`.deb`), arm64 uses **Playwright's Chromium** (Google ships no Chrome
  for arm64 Linux; `/opt/gem/browser-ctl.sh` documents the same fallback) — but
  both land a `/opt/browser/chrome` binary driven over CDP, so the rest of the
  stack is arch-agnostic. The arm64 image was built and verified **natively**
  (all documented functionality, incl. jupyter / browser / MCP, with no
  emulation deadlock).
* `playwright` (used by `browser-sdk`) is installed as a Python package but its
  managed browsers are **not** pre-downloaded — the SDK drives the system Chrome
  over CDP. Run `playwright install chromium` if you need managed browsers.
* `gost` (a generic SOCKS/HTTP proxy) and `ve` (the Volcengine CLI) are optional
  extras; the core sandbox boots without either.
* **`/opt/runtime/nodejs` is intentionally not reproduced.** In the live image it
  is a ~191 MB *installed* `node_modules` cache (no source, only a lock file)
  used by the Node code-execution feature. Nothing on the boot path references
  it; it is repopulated on demand. Recover it verbatim from the image if you
  need the exact preinstalled module set.
* A few env vars in `docker-compose.yaml` are legacy/optional (e.g.
  `TINYPROXY_PORT` — no `tinyproxy` is installed or started); they are kept for
  compatibility but have no backing service.

## Directory map

```
docker/
├── Dockerfile                     # reconstructed build (stages: uvbin, aio-build, ve-build, final)
├── .dockerignore
├── README.md                      # this file
├── apt-packages.txt               # curated apt set (reference)
├── apt-packages.full.txt          # raw `apt-mark showmanual` (reference)
├── requirements/                  # pip freezes recovered from the live image
│   ├── system-python3.10.txt
│   ├── python3.11.txt
│   ├── python3.12.txt             # full freeze (incl. local wheels)
│   └── python3.12.pypi.txt        # freeze minus the two in-house wheels
└── context/                       # build context for COPY/build steps
    ├── rootfs/                    # files copied verbatim into the image
    │   ├── opt/{gem,application,aio,browser-ui,terminal,skills}
    │   ├── usr/local/bin/ve       # platform-dispatch wrapper (compiled CLIs built in stages)
    │   └── var/www/app/static     # recovered front-end assets (browser-ui, swagger-ui, xterm, …)
    ├── python-server/             # recovered source + reconstructed pyproject
    ├── browser-sdk/               # recovered source + reconstructed pyproject
    ├── repl-servers/nodejs/       # recovered in-house Node REPL server
    └── aio/                       # aio CLI re-authored into a buildable package
        ├── src/                   #   buildable ESM source (built by aio-build stage)
        └── recovered-src/         #   provenance: raw slices from the bundle

(ve has no context/ dir — the ve-build stage `go install`s it from upstream.)
```
