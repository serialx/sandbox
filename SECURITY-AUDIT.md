# Security Audit — AIO Sandbox Reconstruction

**Target:** commit `1cb9770` ("Reconstruct the AIO Sandbox Docker image from source")
**Subject:** reconstruction of `ghcr.io/agent-infra/sandbox:1.9.3`
**Scope:** all 353 files / ~59.6k lines added by the commit (Python API server, `aio` CLI, browser SDK, orchestration shell scripts, nginx/supervisord configs, Dockerfile, dependencies)
**Methodology:** 12 parallel Opus finders (one per component / vuln-class) → two-lens adversarial verification per finding (exploitability + classification) → synthesis. Load-bearing findings (#1–#3) independently re-verified by hand against the source.
**Effort:** 51 agents, ~2.5M tokens, 1004 tool uses. 19 raw findings → 12 survived verification.

---

## 1. Bottom line

**No backdoor, hardcoded secret, data exfiltration, or covert build/run-time RCE was found.** The reconstruction is clean of intentional malice.

However, the image **as shipped** contains **two genuine authentication bypasses**, both confirmed by hand, that together yield **unauthenticated remote code execution** and **unauthenticated arbitrary file read** when an operator has enabled auth and exposed the port. Both bypasses are almost certainly reproduced faithfully from upstream `1.9.3` (i.e. *not* introduced by the reconstruction) — but the shipped image is vulnerable regardless of provenance.

> **Severity weighting.** An *authenticated* sandbox user already has in-container shell/code/file execution **by design** — that is the product. So a finding only raises severity if it is an **auth bypass**, a **boundary escape** (out of the container, to the host/cloud), a **credential leak**, or a **backdoor**. Findings are scored on that basis.

---

## 2. Backdoor & intentional-malice check — clean

No deliberate backdoor, hardcoded credential, covert exfiltration, or hidden build/run RCE was identified. Every surviving finding is either an accidental logic bug or an insecure default that the code itself loudly warns about — the opposite of covert. Specifically checked and found benign:

| Area | What was checked | Verdict |
|------|------------------|---------|
| nginx auth gateway | The bypass is an accidental `$request_uri`-includes-query-string mistake with a benign `# Static resources ... no auth required` comment — no magic trigger/value | Accidental, not a backdoor |
| No-auth default | `run.sh:417-471` prints a prominent multi-line `SECURITY RISK - NO AUTHENTICATION CONFIGURED` banner with remediation | Transparent, not covert |
| CLI auto-update | `aio/src/update.js` | No covert download-and-execute |
| Vendored `turndown.browser.umd.js` (979 lines) | Third-party JS bundled into the browser SDK | Untampered; no injected `fetch`/`eval`/`document.cookie` exfil |
| `ve` CLI / `browser-ctl.sh` | `usr/local/bin/ve`, `opt/gem/browser-ctl.sh` | `ve` reads the standard IAM credential file only; `browser-ctl.sh` is unreferenced dead code (no build/runtime caller). The `luban-source.byted.org` HTTP mirror is dead in the default build (Chrome installs over HTTPS) — inherited ByteDance infra, not a planted callback |
| Dockerfile downloads | `Dockerfile:218-300` (fnm, code-server, noVNC, websocat, gost, Chrome `.deb`, Playwright Chromium) | All HTTPS from legitimate vendor origins (GitHub releases, dl.google.com, cdn.playwright.dev). No `curl\|bash` from attacker hosts, no typosquats |
| CDP / browser header rewriting | `cdp.py:62-103`, `browser.py:143-232` | Pure self-reflection of the requester's own `X-Forwarded-*` back to the same requester; no SSRF, no cross-user poisoning, no exfil |
| Network egress | whole tree | No startup callback/beacon/telemetry. Only outbound path is the optional, operator-configured GOST proxy (`PROXY_SERVER`), removed when unset |
| Dependencies | npm lockfiles, apt sources | All 218 npm lockfile entries pin integrity hashes; apt uses signed Ubuntu repos |

---

## 3. Confirmed / likely vulnerabilities

| # | Vulnerability | Location | Class | True severity | Provenance |
|---|---------------|----------|-------|---------------|------------|
| **1** | **Port-proxy Host-header bypass → unauthenticated RCE** | `nginx-server-port-proxy.conf.template`; included at `nginx.conf:66` | auth-bypass | **Critical** | Upstream-inherent (faithfully reproduced) |
| **2** | **Static-extension auth bypass → unauthenticated GET to all `/v1` + `/cdp`** | `nginx-server-with-auth.conf:11-20, 57-71` | auth-bypass | **Critical** | Upstream-inherent (diffed byte-identical to 1.9.3) |
| **3** | **`/v1/file/download` arbitrary-path read (no workspace confinement)** | `app/api/v1/file.py:374-394`; `app/services/file.py:653` | path-traversal | **High** (the unauth sink for #1/#2) | Upstream-inherent |
| **4** | `/v1/proxy/upstream` returns upstream proxy username/password in cleartext | `app/services/proxy_mapping.py:322-356`; route `app/api/v1/proxy_mapping.py:178-190` | credential-leak | **Medium** (High when chained to #2) | Upstream-inherent |
| **5** | Insecure default: `0.0.0.0` bind with auth disabled unless a key is set | `run.sh:421-471`; `nginx-server-without-auth.conf`; sudo grant `run.sh:212` / `Dockerfile:194-195` | insecure-default | **Medium** | Upstream-inherent |
| **6** | Shell/bash subprocesses inherit full server env, exposing `SANDBOX_API_KEY`/JWT keys | `app/core/version.py:267-285`; consumed `app/services/bash.py:247,257`; tmux `app/services/shell.py:258-260` | info-leak | **Low** | Upstream-inherent |
| **7** | Release binaries fetched without checksum/signature verification | `Dockerfile:218-274, 283-300` | supply-chain | **Low** (build hardening) | Upstream-inherent |

### #1 — Port-proxy Host-header bypass → unauthenticated RCE  `Critical`

*Independently verified by hand. Upgraded from the automated panel's "High" after confirming the internal port topology.*

The port-proxy is a sibling `server {}` block on the **same public `8080` socket**, included **unconditionally** (`nginx.conf:66`), keyed by a regex `server_name` and proxying blindly to `127.0.0.1:$port` **with no port allow-list**:

```nginx
# nginx-server-port-proxy.conf.template
server {
    listen ${PUBLIC_LISTEN_IPV4}:${PUBLIC_PORT};      # same 8080 as the auth gateway
    server_name ~^(?<port>\d+)-(.+)$;                 # any "<port>-<anything>" Host
    location / {
        proxy_pass http://127.0.0.1:$port;            # no allow-list / no privileged-port block
        ...
    }
}
```

Default port topology (`Dockerfile:436-438`):

```
PUBLIC_PORT=8080          # public socket (auth gateway + port-proxy both listen here)
AUTH_BACKEND_PORT=8081    # internal "trust everything" routing server
SANDBOX_SRV_PORT=8091     # the FastAPI app itself (127.0.0.1, NO per-route auth)
```

Authentication is enforced **only** by the nginx `/auth` subrequest in front of the *auth-gateway* server block. The FastAPI app on `127.0.0.1:8091` (`cli.py:43`) has no per-route auth. Because nginx selects the server block by matching `Host` against `server_name`, a request with a crafted `Host` selects the port-proxy and is forwarded straight to the app:

```http
POST /v1/shell/exec HTTP/1.1
Host: 8091-x          ← matches the port-proxy regex → proxied to 127.0.0.1:8091, skipping the auth gateway
Content-Type: application/json

{"command": "id"}
```

→ **unauthenticated arbitrary command execution** as the NOPASSWD-sudo sandbox user (`run.sh:212`). `Host: 8081-x` reaches the internal trust-everything backend with the same effect.

**Fix:** reject privileged ports (8080/8081/8091) in the Host regex, or only run the port-proxy behind `auth_request` / on an internal-only socket when auth is enabled.

### #2 — Static-extension auth bypass → unauthenticated GET  `Critical`

*Independently verified. Reported by 6 of the 12 finders, all tracing to one root cause.*

The auth gateway routes by:

```nginx
# nginx-server-with-auth.conf
map "$request_method:$request_uri" $target_workflow {
    default @proxy_with_auth;                                   # everything requires auth...
    "GET:/v1/ping" @proxy_without_auth;
    "~*^GET:.+\.(js|css|svg|woff|json|ico|png|jpg|jpeg|gif|webp|wasm|ttf|woff2)$" @proxy_without_auth;
}
```

`$request_uri` is the **full, unnormalized** request target **including the query string**. The regex only requires the string to *end* in a static extension, so appending a dummy query bypasses auth on any GET route:

```
GET /v1/file/download?path=/etc/shadow&x=.js     → @proxy_without_auth  (no auth)
GET /cdp/json/version?x=.js                       → no auth → full Chrome DevTools control
GET /v1/sandbox?x=.css                            → no auth → sandbox context
GET /v1/shell/sessions?x=.js                      → no auth → session enumeration
GET /v1/proxy/upstream?_=x.json                   → no auth → proxy creds (see #4)
```

The regex anchors `^GET:`, so **POST** mutating/RCE routes are *not* reachable here (RCE comes via #1). The most damaging GET sink is unauthenticated **arbitrary file read** via #3.

**Fix:** key the map on `$uri` (the decoded path, without the query string) instead of `$request_uri`, and add an app-layer auth dependency so nginx is not the sole gate.

### #3 — `/v1/file/download` arbitrary-path read  `High`

`download_file(path: str)` passes the raw client-supplied `path` to `FileResponse(path=path)` after only an **existence** check — no workspace-root confinement, no `..`/symlink/absolute-path guard:

```python
# app/api/v1/file.py:374-394
async def download_file(path: str, change_policy: FileDownloadChangePolicy = Query(...)):
    file_service = services.require('file_service')
    filename = path.split('/')[-1]
    if change_policy == FileDownloadChangePolicy.IGNORE:
        file_service.ensure_file(path)                 # existence check only
        return FileResponse(path=path, filename=filename, media_type='application/octet-stream')
```

For an authenticated user this is unremarkable (they have a shell anyway). Chained with **#2** it becomes **unauthenticated** read of any container-visible file: SSH keys, mounted cloud/IAM credential files, and `/proc/self/environ` — which leaks `SANDBOX_API_KEY` itself (see #6).

**Fix:** resolve and confine `path` to the workspace root (realpath + prefix check, reject symlink escapes); fixing #2 removes the unauthenticated path.

### #4 — `/v1/proxy/upstream` leaks proxy credentials  `Medium` (High chained)

`GET /v1/proxy/upstream` returns the configured corporate egress-proxy `{addr, username, password}` in cleartext (`services/proxy_mapping.py:322-356`). Chained with #2 (`?_=x.json`), an unauthenticated attacker retrieves credentials reusable to egress through the victim's proxy from elsewhere. **Fix:** return `addr` + a `has_credentials` boolean only.

### #5 — Insecure default: open bind, auth off unless a key is set  `Medium`

`run.sh:447` selects the authenticated nginx config **only** when `SANDBOX_JWT_PUBLIC_KEY` or `SANDBOX_API_KEY` is set; otherwise it loads `nginx-server-without-auth.conf` and binds `0.0.0.0`. So `docker run -p 8080:8080 ...` with no key → unauthenticated `POST /v1/shell` RCE as the NOPASSWD-sudo user. This is **not an auth bypass** (no control was configured) and the code prints a loud `SECURITY RISK` banner (`run.sh:452-471`), but it is a foot-gun. **Fix:** fail closed by default or require an explicit opt-in flag to run without auth; document `-p 127.0.0.1:8080:8080`.

### #6 — Master key exposed to sandbox subprocesses  `Low`

`build_sandbox_env()` (`core/version.py:267-285`) passes the full server environment — including `SANDBOX_API_KEY` / `JWT_PUBLIC_KEY` — into spawned shell/bash/tmux processes. An authenticated caller can `printenv SANDBOX_API_KEY` and read the long-lived master key. Only a real escalation under the operator-delegation model (short-lived ticket → permanent key) or when one key is reused across many sandboxes. **Fix:** scrub `SANDBOX_API_KEY` / `JWT_PUBLIC_KEY` / `JWT_PRIVATE_KEY` from `build_sandbox_env()` and the tmux session env.

### #7 — Unverified release binaries  `Low`

`Dockerfile:218-300` downloads release binaries over HTTPS from legitimate origins but without `sha256sum -c` / signature verification. Not reachable via any product request; requires independent compromise of a vendor CDN/release tag. Blast radius is amplified because `chrome-sandbox` is setuid-root (`Dockerfile:303`). **Fix:** add checksum or cosign/GPG verification after each download, especially gost, code-server, and Chrome.

---

## 4. Intended-by-design behaviors (not vulnerabilities)

- **Arbitrary in-container code / shell / file execution** via `/v1/shell`, `/v1/bash`, `/v1/code`, the `/terminal` websocket, and full Chrome/CDP control is the product's purpose. For an *authenticated* user this is expected. The container is the security boundary; the in-container user holds NOPASSWD sudo by design (`run.sh:212`).
- **`convert_to_markdown` SSRF** (`services/utils.py:87-100`), **`/cdp/json/new` arbitrary navigation** (`cdp.py:163-179`), and **wide-open CORS** (`config.py:12` `['*']` + `allow_credentials=True`, `server.py:414-420`) are real primitives but cross no boundary beyond what an authenticated in-sandbox user already has. The CORS wildcard does not escalate because the API authenticates via `X-AIO-API-Key` / `Authorization` / query-param tokens, **not** browser-ambient cookies, so cross-origin credentialed attacks don't apply.

---

## 5. Deployment guidance

1. **Always set `SANDBOX_API_KEY` or `SANDBOX_JWT_PUBLIC_KEY`.** Without one, the sandbox is fully open by design.
2. **Even with auth set, this image is exploitable via #1/#2 as shipped.** Until the nginx configs are patched, bind `-p 127.0.0.1:8080:8080` and front it with your own authenticated reverse proxy — never expose `8080` to an untrusted network.
3. **Never publish to `0.0.0.0` without a key.**
4. **Avoid mounting cloud-credential files** into the container given the unauthenticated file-read primitive (#3).

---

## 6. Lower-confidence / needs-more-info

- **Provenance of #1/#2.** The classification lenses diffed #2 (and #5) against the pristine `1.9.3` image and confirmed byte-identical; #1's mechanics are fully confirmed by hand and lean strongly upstream-inherent, but one verification lens could not diff it in-session. A definitive call requires diffing `nginx-server-port-proxy.conf.template` against the pristine image.
- **#4 reachability** is `partial` — only when an upstream proxy is actually configured *and* #2 is unpatched.
- **#6 impact** hinges on the operator using short-lived tickets/JWTs (not the static key) or reusing one `SANDBOX_API_KEY` across instances.

---

## 7. What was checked and found clean

- **CDP / browser `X-Forwarded-*` rewriting** (`cdp.py:62-103`, `browser.py:143-232`) — self-reflection only; server always uses hardcoded `CDP_BASE_URL=http://127.0.0.1:9222`. Byte-identical to upstream.
- **`/cdp/json/new`** — PUT (not reachable via #2); thin pass-through subject to Chrome's managed `URLBlocklist`/`URLAllowlist`.
- **`convert_to_markdown`** — genuine SSRF/file-read primitive but identical to upstream and reachable only by an already-executing principal; loopback path hardened (`trust_env=False`).
- **App-layer auth** (`services/auth.py:201-256`) — when invoked, correctly validates ticket / API key / JWT with `hmac.compare_digest`; the with-auth gateway genuinely fails closed *when its map isn't bypassed*. (No per-route defense-in-depth — recommended to add.)
- **`browser-ctl.sh`** — unreferenced root-only operator tool, not on any runtime/build path (grep-clean); dead code in the default image.
- **npm dependencies** — all resolved lockfile entries pin integrity hashes; apt uses signed Ubuntu repos. The only unverified fetch path is the Dockerfile binary downloads (#7).

---

## Appendix — methodology detail

- **Finders (12, parallel, Opus):** auth & nginx boundary; shell/bash exec; code/jupyter/nodejs exec; file API; SSRF/proxy/MCP/CDP; browser surface; server core/config/logging; orchestration shell scripts; `aio` CLI (JS); browser SDK (Python); supply-chain/Dockerfile; cross-cutting backdoor sweep.
- **Verification (per finding, 2 lenses, Opus):** an *exploitability/reachability* lens (is it reachable without auth? is input attacker-controlled along the full path?) and a *classification/severity* lens (genuine issue vs intended behavior; backdoor vs accidental; reconstruction-introduced vs upstream-inherent; true severity given the auth boundary). Findings dismissed by both lenses were dropped (7 of 19).
- **Manual re-verification:** findings #1, #2, #3 (and the #5 auth-toggle and port topology) were re-read directly from source by the lead reviewer.
