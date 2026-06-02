# syntax=docker/dockerfile:1.7
# =============================================================================
# AIO Sandbox (all-in-one-sandbox) — reconstructed Dockerfile
# =============================================================================
# Reverse-engineered from a running `ghcr.io/agent-infra/sandbox:latest`
# container (which is a mirror of
# enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox).
# The upstream build recipe is not public, so this file reproduces the image
# from first principles:
#
#   * Public components  -> installed from upstream (apt / pip / npm / releases)
#   * In-house components -> rebuilt from source recovered out of the image
#                            (see ./context/*) : python-server, browser-sdk,
#                            the nodejs REPL server, the /opt/gem orchestration
#                            layer, and the `aio` CLI bundle.
#   * Browser            -> plain google-chrome-stable (the original /opt/browser
#                            is just a flattened google-chrome-stable .deb).
#
# Build context root is this `docker/` directory:
#     docker build -t aio-sandbox -f docker/Dockerfile docker/
#
# See ./README.md for the full reconstruction notes and known caveats.
# -----------------------------------------------------------------------------

# ----- pinned versions (observed in the live image) --------------------------
ARG PY311_VERSION=3.11.13
ARG PY312_VERSION=3.12.11
ARG NODE20_VERSION=20.20.2
ARG NODE22_VERSION=22.22.2
ARG NODE24_VERSION=24.15.0
ARG FNM_VERSION=1.38.1
ARG UV_VERSION=0.8.9
ARG BUN_VERSION=1.3.3
ARG MCP_SERVER_BROWSER_VERSION=1.2.29
ARG AGENT_BROWSER_VERSION=0.22.3
ARG CHROME_DEVTOOLS_MCP_VERSION=0.9.0
ARG CODE_SERVER_VERSION=4.104.0
ARG NOVNC_VERSION=1.4.0
ARG WEBSOCAT_VERSION=1.13.0
ARG GOST_VERSION=3.0.0-rc10
ARG CHROME_DEB_VERSION=139.0.7258.127-1
# arm64 Linux has no Google Chrome build, so on arm64 the browser is Playwright's
# Chromium (the same fallback the recovered /opt/gem/browser-ctl.sh uses). This
# is its build number.
ARG PLAYWRIGHT_CHROMIUM_BUILD=1194
ARG VE_VERSION=1.0.43

# uv binary (used to provision the python-build-standalone interpreters and to
# build the recovered in-house wheels). Pulled from the official image.
FROM ghcr.io/astral-sh/uv:0.8.9 AS uvbin

# -----------------------------------------------------------------------------
# `aio` CLI — built from source re-authored out of the original esbuild bundle.
# The recovered multi-file ESM source lives in context/aio/src/; esbuild bundles
# it back into a single self-contained node script, byte-for-byte ~equivalent to
# (and behaviourally identical to) the original /usr/local/bin/aio. See
# context/aio/README.md.
# -----------------------------------------------------------------------------
FROM node:22-slim AS aio-build
WORKDIR /build/aio
COPY context/aio/package.json context/aio/package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY context/aio/build.mjs ./build.mjs
COPY context/aio/src ./src
RUN npm run build   # -> /build/aio/dist/aio.js

# -----------------------------------------------------------------------------
# Static web assets served by nginx at /static/sandbox/ — fetched from pinned
# upstream npm packages at build time instead of being vendored into git. Only
# the prebuilt bundles below are copied into the final image (byte-identical to
# the original 1.9.3 image; verified by hash). `npm ci` pulls some packages'
# full dep trees (e.g. @agent-infra/browser-ui drags in puppeteer-core) into
# this throwaway stage, but none of that reaches the image. --ignore-scripts:
# we only need files already shipped in the tarballs, never install hooks.
# NOTE: favicon.png is not an npm package and stays in context/rootfs.
# -----------------------------------------------------------------------------
FROM node:22-slim AS static-assets
WORKDIR /assets
COPY context/static-assets/package.json context/static-assets/package-lock.json ./
RUN npm ci --no-fund --no-audit --ignore-scripts
RUN set -eux; \
    D=/out/var/www/app/static/sandbox; \
    mkdir -p "$D/swagger-ui-dist@5" "$D/xterm@5.3.0/lib" "$D/xterm@5.3.0/css" \
             "$D/xterm-addon-fit@0.8.0/lib" "$D/clipboard@2.0.11" \
             "$D/agent-infra-browser-ui@0.2.2"; \
    cp node_modules/swagger-ui-dist/swagger-ui-bundle.js          "$D/swagger-ui-dist@5/swagger-ui-bundle.js"; \
    cp node_modules/swagger-ui-dist/swagger-ui.css                "$D/swagger-ui-dist@5/swagger-ui.css"; \
    cp node_modules/xterm/lib/xterm.js                            "$D/xterm@5.3.0/lib/xterm.js"; \
    cp node_modules/xterm/css/xterm.css                           "$D/xterm@5.3.0/css/xterm.css"; \
    cp node_modules/xterm-addon-fit/lib/xterm-addon-fit.js        "$D/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.js"; \
    cp node_modules/clipboard/dist/clipboard.min.js               "$D/clipboard@2.0.11/clipboard.min.js"; \
    cp node_modules/@agent-infra/browser-ui/dist/bundle/index.js              "$D/agent-infra-browser-ui@0.2.2/index.js"; \
    cp node_modules/@agent-infra/browser-ui/dist/bundle/index.js.LICENSE.txt  "$D/agent-infra-browser-ui@0.2.2/index.js.LICENSE.txt"

# -----------------------------------------------------------------------------
# `ve` (volcengine CLI) — built from public Go source. The recovered binary's
# Go buildinfo identifies it exactly as github.com/volcengine/volcengine-cli
# (deps cobra/pflag/uuid/volc-sdk-golang/volcengine-go-sdk). goreleaser builds
# it CGO-free as a static `ve`; we reproduce that. (Off the sandbox boot path —
# a Volcengine cloud CLI used only with IAM credentials.)
# -----------------------------------------------------------------------------
FROM golang:1.23-bookworm AS ve-build
ARG VE_VERSION=1.0.43
ARG TARGETARCH
ENV CGO_ENABLED=0 GOOS=linux GOARCH=${TARGETARCH}
RUN go install -trimpath -ldflags "-s -w -X main.version=${VE_VERSION}" \
      github.com/volcengine/volcengine-cli@v${VE_VERSION}
# -> /go/bin/volcengine-cli

# =============================================================================
# Final image
# =============================================================================
FROM ubuntu:22.04

ARG PY311_VERSION
ARG PY312_VERSION
ARG NODE20_VERSION
ARG NODE22_VERSION
ARG NODE24_VERSION
ARG FNM_VERSION
ARG BUN_VERSION
ARG MCP_SERVER_BROWSER_VERSION
ARG AGENT_BROWSER_VERSION
ARG CHROME_DEVTOOLS_MCP_VERSION
ARG CODE_SERVER_VERSION
ARG NOVNC_VERSION
ARG WEBSOCAT_VERSION
ARG GOST_VERSION
ARG CHROME_DEB_VERSION
ARG PLAYWRIGHT_CHROMIUM_BUILD
# Target architecture (amd64 | arm64), set automatically by BuildKit. Drives the
# per-arch release assets selected throughout this stage.
ARG TARGETARCH

ENV DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# -----------------------------------------------------------------------------
# 1. System packages
#    Curated from `apt-mark showmanual` on the live image (minus base/priority
#    packages that ship with ubuntu:22.04). Grouped by purpose.
# -----------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
      # --- toolchain / build deps (native python & node module builds) ---
      build-essential gcc make cmake ninja-build meson pkg-config gettext gawk \
      libffi-dev libcairo2-dev libpango1.0-dev freeglut3-dev python3-dev \
      # --- core CLI utilities ---
      ca-certificates curl wget git gh jq file unzip zip tree htop lsof psmisc \
      rsync openssh-client sudo software-properties-common gnupg vim nano less \
      net-tools netcat-openbsd iputils-ping telnet nmap tmux tilix lsyncd \
      ripgrep \
      # --- python (system 3.10) ---
      python3 python3-pip python3-tk \
      # --- web gateway ---
      nginx \
      # --- virtual desktop: X / VNC / window manager / clipboard ---
      tigervnc-standalone-server openbox autocutsel \
      x11-utils x11-xserver-utils xauth xclip xdotool xdg-utils \
      gnome-screenshot dbus dbus-x11 \
      # --- CJK input method (fcitx5) ---
      fcitx5 fcitx5-chinese-addons fcitx5-frontend-gtk3 fcitx5-frontend-qt5 \
      # --- media / document processing (manim, weasyprint, opencv, ...) ---
      ffmpeg imagemagick \
      # --- chrome / GUI runtime libraries ---
      libnss3 libnspr4 libgbm1 libdrm2 libgtk-3-0 libasound2 libcups2 \
      libatk1.0-0 libatk-bridge2.0-0 libatspi2.0-0 libxcomposite1 libxdamage1 \
      libxfixes3 libxrandr2 libxkbcommon0 libwayland-client0 libvulkan1 \
      libxext6 libxv1 libgcrypt20 libglib2.0-0 libgl1 libcairo2 libpango-1.0-0 \
      # --- fonts (Latin / CJK / Indic / emoji / ...) ---
      fontconfig fonts-dejavu fonts-liberation fonts-noto-core fonts-noto-extra \
      fonts-noto-cjk fonts-noto-cjk-extra fonts-noto-color-emoji \
      fonts-arphic-ukai fonts-arphic-uming fonts-unfonts-core \
      fonts-ipafont-gothic fonts-ipafont-mincho fonts-takao-mincho \
      fonts-indic fonts-khmeros fonts-lao fonts-thai-tlwg fonts-sil-padauk \
      xfonts-intl-chinese \
      # --- locales / timezone ---
      locales tzdata \
    && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# 2. Locale
# -----------------------------------------------------------------------------
RUN sed -i -e 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' \
           -e 's/# zh_CN.UTF-8 UTF-8/zh_CN.UTF-8 UTF-8/' /etc/locale.gen \
    && locale-gen
ENV LANG=en_US.UTF-8 LANGUAGE=en_US:en LC_ALL=en_US.UTF-8

# -----------------------------------------------------------------------------
# 3. Non-root user `gem` (uid/gid 1000) + passwordless sudo
# -----------------------------------------------------------------------------
ENV USER=gem
RUN groupadd -g 1000 gem \
    && useradd -m -u 1000 -g 1000 -s /bin/bash gem \
    && echo 'gem ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/gem \
    && chmod 0440 /etc/sudoers.d/gem

# -----------------------------------------------------------------------------
# 4. uv (provisioner + wheel builder)
# -----------------------------------------------------------------------------
COPY --from=uvbin /uv /uvx /usr/local/bin/

# -----------------------------------------------------------------------------
# 5. Python 3.11 + 3.12 (python-build-standalone, provisioned via uv)
#    The live image's interpreters were built by python-build-standalone (the
#    CONFIG_ARGS signature `--with-openssl=/tools/deps ... --enable-bolt CC=clang`
#    is unmistakable); uv installs the very same builds.
# -----------------------------------------------------------------------------
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python
RUN uv python install ${PY311_VERSION} ${PY312_VERSION} \
    && UVARCH="$(case ${TARGETARCH} in arm64) echo aarch64;; *) echo x86_64;; esac)" \
    && ln -sfn /opt/uv-python/cpython-${PY311_VERSION}-linux-${UVARCH}-gnu /opt/python3.11 \
    && ln -sfn /opt/uv-python/cpython-${PY312_VERSION}-linux-${UVARCH}-gnu /opt/python3.12

# -----------------------------------------------------------------------------
# 6. Node.js 20 / 22 / 24 via fnm (FNM_DIR=/opt/fnm), node 22 is primary
# -----------------------------------------------------------------------------
ENV FNM_DIR=/opt/fnm
RUN FNM_ASSET="$(case ${TARGETARCH} in arm64) echo fnm-arm64.zip;; *) echo fnm-linux.zip;; esac)" \
    && curl -fsSL -o /tmp/fnm.zip \
      "https://github.com/Schniz/fnm/releases/download/v${FNM_VERSION}/${FNM_ASSET}" \
    && unzip -o /tmp/fnm.zip -d /tmp/fnm && install -m755 /tmp/fnm/fnm /usr/local/bin/fnm \
    && rm -rf /tmp/fnm.zip /tmp/fnm \
    && for v in ${NODE20_VERSION} ${NODE22_VERSION} ${NODE24_VERSION}; do fnm install "$v"; done \
    && mkdir -p /opt/nodejs \
    && ln -sfn /opt/fnm/node-versions/v${NODE20_VERSION}/installation /opt/nodejs/20 \
    && ln -sfn /opt/fnm/node-versions/v${NODE22_VERSION}/installation /opt/nodejs/22 \
    && ln -sfn /opt/fnm/node-versions/v${NODE24_VERSION}/installation /opt/nodejs/24

# -----------------------------------------------------------------------------
# 7. Global npm packages (installed into node 22) + agent-browser rename
#    `agent-browser` is renamed to `agent-browser-real` so the recovered
#    /opt/gem/agent-browser-wrapper.sh can inject the shared-CDP flag.
# -----------------------------------------------------------------------------
# (`node` is not yet on PATH here — the /usr/local/bin/node symlinks are wired
#  up in §14 — so put node 22's bin on PATH for this RUN; npm's shebang is
#  `#!/usr/bin/env node`. The final runtime PATH is unchanged.)
RUN export PATH="/opt/nodejs/22/bin:$PATH" \
    && npm install -g --no-fund --no-audit \
      bun@${BUN_VERSION} \
      yarn \
      @agent-infra/mcp-server-browser@${MCP_SERVER_BROWSER_VERSION} \
      agent-browser@${AGENT_BROWSER_VERSION} \
      chrome-devtools-mcp@${CHROME_DEVTOOLS_MCP_VERSION} \
    && mv /opt/nodejs/22/bin/agent-browser /opt/nodejs/22/bin/agent-browser-real

# -----------------------------------------------------------------------------
# 8. code-server, noVNC, websocat, gost
# -----------------------------------------------------------------------------
RUN RUSTARCH="$(case ${TARGETARCH} in arm64) echo aarch64;; *) echo x86_64;; esac)" \
    && curl -fsSL -o /tmp/code-server.tar.gz \
      "https://github.com/coder/code-server/releases/download/v${CODE_SERVER_VERSION}/code-server-${CODE_SERVER_VERSION}-linux-${TARGETARCH}.tar.gz" \
    && mkdir -p /usr/local/code-server-${CODE_SERVER_VERSION} \
    && tar -xzf /tmp/code-server.tar.gz -C /usr/local/code-server-${CODE_SERVER_VERSION} --strip-components=1 \
    && ln -sf /usr/local/code-server-${CODE_SERVER_VERSION}/bin/code-server /usr/bin/code-server \
    && rm -f /tmp/code-server.tar.gz \
    # noVNC (static web client)
    && curl -fsSL -o /tmp/novnc.tar.gz \
      "https://github.com/novnc/noVNC/archive/refs/tags/v${NOVNC_VERSION}.tar.gz" \
    && mkdir -p /opt/novnc \
    && tar -xzf /tmp/novnc.tar.gz -C /opt/novnc --strip-components=1 \
    # noVNC ships vnc.html but no index.html; the live image serves /vnc/index.html \
    # (a copy of vnc.html) — nginx's `index index.html vnc.html;` and the documented \
    # /vnc/index.html?autoconnect=true URL both expect it. \
    && cp /opt/novnc/vnc.html /opt/novnc/index.html \
    && rm -f /tmp/novnc.tar.gz \
    # websocat (VNC websocket bridge)
    && curl -fsSL -o /usr/local/bin/websocat \
      "https://github.com/vi/websocat/releases/download/v${WEBSOCAT_VERSION}/websocat.${RUSTARCH}-unknown-linux-musl" \
    && chmod +x /usr/local/bin/websocat \
    # gost (proxy tunnel, used when PROXY_SERVER is set)
    && curl -fsSL -o /tmp/gost.tar.gz \
      "https://github.com/go-gost/gost/releases/download/v${GOST_VERSION}/gost_${GOST_VERSION}_linux_${TARGETARCH}.tar.gz" \
    && tar -xzf /tmp/gost.tar.gz -C /usr/local/bin gost \
    && chmod +x /usr/local/bin/gost && rm -f /tmp/gost.tar.gz

# -----------------------------------------------------------------------------
# 9. Browser at /opt/browser (binary: /opt/browser/chrome).
#    - amd64: flattened google-chrome-stable .deb (matches the live image).
#    - arm64: Playwright's Chromium build (no Google Chrome exists for arm64
#      Linux); /opt/gem/browser-ctl.sh uses the same fallback. Both unpack to a
#      /opt/browser/chrome binary, so the rest of the stack is arch-agnostic.
# -----------------------------------------------------------------------------
RUN set -eux; \
    mkdir -p /opt/browser; \
    case "${TARGETARCH}" in \
      arm64) \
        curl -fsSL -o /tmp/chromium.zip \
          "https://cdn.playwright.dev/dbazure/download/playwright/builds/chromium/${PLAYWRIGHT_CHROMIUM_BUILD}/chromium-linux-arm64.zip"; \
        unzip -q /tmp/chromium.zip -d /tmp/cr; \
        cp -a /tmp/cr/chrome-linux/. /opt/browser/; \
        rm -rf /tmp/chromium.zip /tmp/cr; \
        ;; \
      *) \
        curl -fsSL -o /tmp/chrome.deb \
          "https://dl.google.com/linux/chrome/deb/pool/main/g/google-chrome-stable/google-chrome-stable_${CHROME_DEB_VERSION}_amd64.deb"; \
        dpkg-deb -x /tmp/chrome.deb /tmp/chrome-x; \
        cp -a /tmp/chrome-x/opt/google/chrome/. /opt/browser/; \
        rm -rf /tmp/chrome.deb /tmp/chrome-x; \
        ;; \
    esac; \
    # setuid sandbox helper (present on chrome; may be absent on chromium — we \
    # run with --no-sandbox anyway, so its absence is harmless) \
    if [ -f /opt/browser/chrome-sandbox ]; then chmod 4755 /opt/browser/chrome-sandbox; fi; \
    update-alternatives --install /usr/bin/browser browser /opt/browser/chrome 100; \
    ln -sf /etc/alternatives/browser /usr/local/bin/browser; \
    ln -sf /usr/local/bin/browser /usr/local/bin/chromium; \
    ln -sf /usr/local/bin/browser /usr/local/bin/chromium-browser; \
    ln -sf /usr/local/bin/browser /usr/local/bin/chromium-browser-stable

# -----------------------------------------------------------------------------
# 10. Python dependency sets (reproduced from the live `pip freeze`s)
#     - system python 3.10 : user-facing tooling + supervisord (manim,
#                            weasyprint, opencv, yt-dlp, supervisor@git, ...)
#     - /opt/python3.11    : lightweight matplotlib/ipykernel data kernel
#     - /opt/python3.12    : sandbox server runtime + JupyterLab + markitdown
# -----------------------------------------------------------------------------
COPY requirements/ /tmp/requirements/
RUN python3 -m pip install --no-cache-dir --upgrade pip setuptools wheel \
    && python3 -m pip install --no-cache-dir -r /tmp/requirements/system-python3.10.txt \
    && ln -sf /usr/local/bin/supervisord /usr/bin/supervisord
# (uv's python-build-standalone interpreters ship a PEP-668 EXTERNALLY-MANAGED
#  marker, so pip refuses to install without --break-system-packages. The apt
#  system python3.10 above has no such marker.)
RUN /opt/python3.11/bin/python -m pip install --no-cache-dir --break-system-packages -r /tmp/requirements/python3.11.txt
RUN /opt/python3.12/bin/python -m pip install --no-cache-dir --break-system-packages -r /tmp/requirements/python3.12.pypi.txt

# -----------------------------------------------------------------------------
# 11. In-house Python packages — rebuilt from recovered source into wheels and
#     installed into /opt/python3.12 (matches the original /tmp/wheels install).
# -----------------------------------------------------------------------------
COPY context/browser-sdk   /usr/src/browser-sdk
COPY context/python-server /usr/src/python-server
RUN uv build --wheel --out-dir /tmp/wheels /usr/src/browser-sdk \
    && uv build --wheel --out-dir /tmp/wheels /usr/src/python-server \
    && /opt/python3.12/bin/python -m pip install --no-cache-dir --break-system-packages --no-deps \
         /tmp/wheels/browser_sdk-*.whl /tmp/wheels/python_server-*.whl \
    && rm -rf /tmp/wheels /usr/src/browser-sdk /usr/src/python-server

# -----------------------------------------------------------------------------
# 12. Node.js REPL server (in-house, recovered) — install its runtime deps
# -----------------------------------------------------------------------------
COPY context/repl-servers/nodejs /opt/repl-servers/nodejs
RUN export PATH="/opt/nodejs/22/bin:$PATH" \
    && cd /opt/repl-servers/nodejs && npm install --omit=dev --no-fund --no-audit

# -----------------------------------------------------------------------------
# 13. Orchestration layer (recovered verbatim — these *are* the source: shell
#     scripts + config): /opt/gem (run.sh, supervisord & nginx configs,
#     browser/jupyter/code-server launchers), /opt/application (entrypoint),
#     static UIs, skills, and the `ve` dispatch wrapper. The two compiled CLIs
#     (`aio`, `ve`) are built from source in the stages above and dropped in next.
# -----------------------------------------------------------------------------
COPY context/rootfs/ /
# the re-authored aio CLI, built from source in the aio-build stage
COPY --from=aio-build /build/aio/dist/aio.js /usr/local/bin/aio
# volcengine CLI, built from source in the ve-build stage (the recovered `ve`
# dispatch wrapper execs linux_${arch}_ve based on `uname -m`).
COPY --from=ve-build /go/bin/volcengine-cli /usr/local/bin/linux_${TARGETARCH}_ve
# static web assets (xterm, swagger-ui, clipboard, browser-ui) fetched from
# pinned npm in the static-assets stage instead of vendored into git.
COPY --from=static-assets /out/ /

# -----------------------------------------------------------------------------
# 14. Wire up /usr/local/bin + /etc symlinks exactly as the live image has them
# -----------------------------------------------------------------------------
RUN set -eux; \
    # node / npm / npx (versioned + primary = 22) \
    for n in 20 22 24; do \
      ln -sf /opt/nodejs/$n/bin/node /usr/local/bin/node$n; \
      ln -sf /opt/nodejs/$n/bin/npm  /usr/local/bin/npm$n; \
      ln -sf /opt/nodejs/$n/bin/npx  /usr/local/bin/npx$n; \
    done; \
    for b in node npm npx corepack yarn yarnpkg bun bunx \
             mcp-server-browser chrome-devtools-mcp agent-browser-real; do \
      ln -sf /opt/nodejs/22/bin/$b /usr/local/bin/$b; \
    done; \
    # python interpreters + entrypoints \
    ln -sf /opt/python3.11/bin/python    /usr/local/bin/python3.11; \
    ln -sf /opt/python3.12/bin/python    /usr/local/bin/python3.12; \
    ln -sf /opt/python3.11/bin/pip3      /usr/local/bin/pip3.11; \
    ln -sf /opt/python3.12/bin/pip3      /usr/local/bin/pip3.12; \
    ln -sf /opt/python3.12/bin/python-server /usr/local/bin/python-server; \
    # in-house CLIs (copied via rootfs) + wrapper wiring \
    ln -sf /opt/gem/agent-browser-wrapper.sh /usr/local/bin/agent-browser; \
    ln -sf /opt/gem/xdg-settings             /usr/local/bin/xdg-settings; \
    chmod +x /usr/local/bin/aio /usr/local/bin/ve /usr/local/bin/linux_${TARGETARCH}_ve; \
    find /opt/gem -name '*.sh' -exec chmod +x {} +; \
    chmod +x /opt/application/run.sh; \
    # supervisord config location expected by run.sh \
    ln -sf /opt/gem/supervisord.conf /etc/supervisord.conf; \
    # node/fnm env for shells; run.sh sources this file under `set -e` \
    mkdir -p /etc/profile.d; \
    ln -sf /opt/gem/nodejs.sh /etc/profile.d/nodejs.sh; \
    # fnm "default" alias -> node 22, so the per-user multishell symlink \
    # ($HOME/.fnm_shell created by nodejs.sh) resolves and `node` works in shells \
    mkdir -p /opt/fnm/aliases; \
    ln -sfn /opt/fnm/node-versions/v${NODE22_VERSION}/installation /opt/fnm/aliases/default; \
    # jupyter runtime/data dirs (gem-owned) + user npm-global bin \
    mkdir -p /opt/jupyter/runtime /opt/jupyter/data /home/gem/.npm-global/bin; \
    chown -R gem:gem /home/gem /opt/jupyter
# (/opt/gem and /opt/application stay root-owned, as in the original image)

# -----------------------------------------------------------------------------
# 15. Runtime environment. Values are reproduced from the live image's own
#     environment (recovered via `env` on a running container); k8s-injected
#     vars (KUBERNETES_*, TALOS_*, OTEL auto-instrumentation SRV_PYTHONPATH, …)
#     are intentionally omitted. run.sh derives the rest (AUTOSTART_*, the nginx
#     listen config, gost, …) at boot.
# -----------------------------------------------------------------------------
# node 22/20/24 bins are on PATH ahead of the system dirs, as in the original.
ENV PATH=/opt/nodejs/22/bin:/opt/nodejs/20/bin:/opt/nodejs/24/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    NODEJS_DIR=/opt/nodejs \
    WORKSPACE="" \
    HOMEPAGE="" \
    TZ=Asia/Singapore \
    LANG=en_US.UTF-8 LANGUAGE=en_US:en LC_ALL=en_US.UTF-8 \
    CC=gcc CXX=g++ \
    # --- virtual display / VNC --- \
    DISPLAY=:99.0 \
    DISPLAY_DEPTH=24 \
    DISPLAY_WIDTH=1280 \
    DISPLAY_HEIGHT=1024 \
    VNC_SERVER_PORT=5900 \
    WEBSOCKET_PROXY_PORT=6080 \
    # --- user / logging --- \
    USER_UID=1000 \
    USER_GID=1000 \
    LOG_DIR=/var/log/gem \
    LOG_STDOUT_SERVER=false \
    # --- gateway readiness (nginx waits on these before starting) --- \
    WAIT_PORTS=8091 \
    WAIT_FILES="" \
    WAIT_TIMEOUT=300 \
    WAIT_INTERVAL=0.25 \
    # --- service ports --- \
    PUBLIC_PORT=8080 \
    AUTH_BACKEND_PORT=8081 \
    SANDBOX_SRV_PORT=8091 \
    JUPYTER_LAB_PORT=8888 \
    CODE_SERVER_PORT=8200 \
    MCP_SERVER_BROWSER_PORT=8100 \
    OPENCODE_PORT=4096 \
    TINYPROXY_PORT=8118 \
    # --- code execution runtimes --- \
    NODE_CODE_EXEC_VERSION=node22 \
    PYTHON_CODE_EXEC_VERSION=python3 \
    DISABLE_NODEJS_REPL=false \
    NODEJS_REPL_PORT="" \
    NODEJS_REPL_PORT_20=8192 \
    NODEJS_REPL_PORT_22=8092 \
    NODEJS_REPL_PORT_24=8392 \
    MAX_SHELL_SESSIONS=10 \
    # --- browser (chrome over CDP + agent-browser/puppeteer) --- \
    BROWSER_REMOTE_DEBUGGING_PORT=9222 \
    BROWSER_EXECUTABLE_PATH=/usr/local/bin/browser \
    AGENT_BROWSER_EXECUTABLE_PATH=/usr/local/bin/browser \
    PUPPETEER_EXECUTABLE_PATH=/usr/local/bin/browser \
    PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true \
    BROWSER_NO_SANDBOX=--no-sandbox \
    BROWSER_LANG=en-US \
    CHROME_UI_LANG="" \
    BROWSER_USER_AGENT="" \
    BROWSER_EXTRA_ARGS="" \
    BROWSER_DOWNLOAD_DIR="" \
    BROWSER_INIT_COOKIES="" \
    BROWSER_URL_ALLOWLIST="" \
    BROWSER_URL_BLOCKLIST="" \
    BROWSER_COMMANDLINE_ARGS="--disable-backgrounding-occluded-windows --disable-background-timer-throttling --disable-blink-features=AutomationControlled --disable-dev-shm-usage --disable-external-intent-requests --disable-features=IPH_DesktopCustomizeChrome,IsolateOrigins,site-per-proces,Translate --disable-focus-on-load --disable-gpu --disable-infobars --disable-popup-blocking --disable-prompt-on-repost --disable-renderer-backgrounding --disable-site-isolation-trials --disable-window-activation --mute-audio --no-default-browser-check --no-first-run --noerrdialogs --remote-allow-origins=* --remote-debugging-port=9222 --suppress-message-center-popups --start-maximized" \
    # --- feature toggles (match the live image: all services on, CJK IME on) --- \
    DISABLE_JUPYTER=false \
    DISABLE_CODE_SERVER=false \
    DISABLE_BROWSER=false \
    DISABLE_MCP_BROWSER=false \
    DISABLE_VNC=false \
    ENABLE_CJK_IME=true \
    # --- MCP / proxy / DNS / auth --- \
    EXTRA_MCP_SERVERS="" \
    DNS_OVER_HTTPS_TEMPLATES="" \
    PROXY_BYPASS_LIST="" \
    JWT_PUBLIC_KEY="" \
    # --- aio CLI + lifecycle hooks --- \
    AIO_BASE_URL=http://localhost:8091 \
    AIO_SKILLS_PATH="" \
    RUN_HOOK_INIT="" \
    RUN_HOOK_PRE_SERVICES="" \
    RUN_HOOK_POST_READY="" \
    RUN_HOOKS_STRICT=false \
    SANDBOX_SHUTDOWN_HOOKS="" \
    SANDBOX_SHUTDOWN_HOOKS_TIMEOUT=30 \
    FAAS_SANDBOX_RUNTIME_INJECTION_ENABLE_SANDBOXD=false \
    OTEL_SDK_DISABLED=false \
    OTEL_PYTHON_DISABLED_INSTRUMENTATIONS=redis

EXPOSE 8080

WORKDIR /home/gem

# supervisord runs as root (PID 1) and drops privileges to `gem` per-program.
CMD ["/opt/application/run.sh"]
