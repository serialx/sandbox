from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from pathlib import Path
from urllib.parse import urlparse

import httpx

import re
import shutil

from app.schemas.proxy_mapping import ProxyHealthCheck, ProxyMappingRoute, ProxyUpstreamInfo

logger = logging.getLogger(__name__)

STATE_DIR = Path('/var/lib/aio-sandbox')
PROXY_MAP_JSON = STATE_DIR / 'proxy-map.json'
NGINX_CONF = Path('/opt/gem/nginx-proxy-map.conf')
GOST_HOSTS_FILE = Path('/opt/gem/gost-hosts.txt')
GOST_BYPASS_FILE = Path('/opt/gem/gost-bypass.txt')
USER_BYPASS_JSON = STATE_DIR / 'proxy-map-user-bypasses.json'
# All proxy-map nginx server blocks listen on this single port.
# GOST hosts resolves mapped domains to 127.0.0.1, and the default HTTP port (80)
# is used for the connection. nginx uses server_name to differentiate domains.
NGINX_PROXY_MAP_PORT = 80
NGINX_PROXY_MAP_HTTPS_PORT = 443
PROXY_MAP_TLS_CRT = Path('/opt/gem/proxy-map-tls.crt')
PROXY_MAP_TLS_KEY = Path('/opt/gem/proxy-map-tls.key')
GOST_API_URL = os.getenv('GOST_API_URL', 'http://127.0.0.1:18080')
NGINX_RELOAD_DEBOUNCE_SECONDS = 1.0


class ProxyMappingService:

    def __init__(
        self,
        proxy_map_json: Path = PROXY_MAP_JSON,
        nginx_conf: Path = NGINX_CONF,
        gost_hosts_file: Path = GOST_HOSTS_FILE,
        gost_bypass_file: Path = GOST_BYPASS_FILE,
        user_bypass_json: Path = USER_BYPASS_JSON,
        tls_crt: Path = PROXY_MAP_TLS_CRT,
        tls_key: Path = PROXY_MAP_TLS_KEY,
    ):
        self._lock = asyncio.Lock()
        self._proxy_map_json = proxy_map_json
        self._nginx_conf = nginx_conf
        self._gost_hosts_file = gost_hosts_file
        self._gost_bypass_file = gost_bypass_file
        self._user_bypass_json = user_bypass_json
        self._tls_crt = tls_crt
        self._tls_key = tls_key
        self._reload_task: asyncio.Task | None = None
        self._gost_api_url = GOST_API_URL
        self._auth_watcher_task: asyncio.Task | None = None

    # --- Mappings CRUD ---

    async def list_mappings(self) -> list[ProxyMappingRoute]:
        return await asyncio.to_thread(self._load_mappings)

    async def add_mapping(self, source: str, target: str) -> ProxyMappingRoute:
        async with self._lock:
            mappings = await asyncio.to_thread(self._load_mappings)

            # Expand target shorthand: ":3000" -> "127.0.0.1:3000"
            if target.startswith(':'):
                target = f'127.0.0.1{target}'

            source_host = self._extract_host(source)
            source_path = self._extract_path(source)

            # Check duplicate — update if source matches
            for m in mappings:
                if m.source == source:
                    m.target = target
                    await self._apply(mappings)
                    return m

            entry = ProxyMappingRoute(
                source=source,
                target=target,
                source_host=source_host,
                source_path=source_path,
                internal_port=NGINX_PROXY_MAP_PORT,
            )
            mappings.append(entry)
            await self._apply(mappings)
            return entry

    async def remove_mapping(self, source: str) -> bool:
        async with self._lock:
            mappings = await asyncio.to_thread(self._load_mappings)
            original_len = len(mappings)
            mappings = [m for m in mappings if m.source != source]
            if len(mappings) == original_len:
                return False
            await self._apply(mappings)
            return True

    # --- Bypass (exclude/include) ---

    async def list_bypasses(self) -> list[str]:
        """Return user-added bypass patterns (not auto-generated ones)."""
        return await asyncio.to_thread(self._load_user_bypasses)

    async def add_bypass(self, pattern: str) -> bool:
        async with self._lock:
            user_patterns = await asyncio.to_thread(self._load_user_bypasses)
            if pattern in user_patterns:
                return False
            user_patterns.append(pattern)
            await asyncio.to_thread(self._save_user_bypasses, user_patterns)
            # Regenerate full bypass file
            mappings = await asyncio.to_thread(self._load_mappings)
            await asyncio.to_thread(self._regenerate_bypass_file, mappings)
            return True

    async def remove_bypass(self, pattern: str) -> bool:
        async with self._lock:
            user_patterns = await asyncio.to_thread(self._load_user_bypasses)
            if pattern not in user_patterns:
                return False
            user_patterns.remove(pattern)
            await asyncio.to_thread(self._save_user_bypasses, user_patterns)
            # Regenerate full bypass file
            mappings = await asyncio.to_thread(self._load_mappings)
            await asyncio.to_thread(self._regenerate_bypass_file, mappings)
            return True

    # --- Diagnose ---

    async def diagnose(self, url: str) -> dict:
        parsed = urlparse(url)
        hostname = parsed.hostname or ''
        url_path = parsed.path or '/'
        scheme = parsed.scheme or 'http'
        mappings = await asyncio.to_thread(self._load_mappings)

        # Find all host-matching mappings, then pick best path match (longest prefix)
        candidates = [m for m in mappings if self._host_matches(hostname, m.source_host)]
        matched = None
        if candidates:
            # Sort by path length descending — longest prefix wins (like nginx)
            candidates.sort(key=lambda m: len(m.source_path), reverse=True)
            for m in candidates:
                if url_path.startswith(m.source_path) or m.source_path == '/':
                    matched = m
                    break

        if matched:
            target_host_port = matched.target.split('/')[0]
            host_part, _, port_str = target_host_port.rpartition(':')
            try:
                port = int(port_str)
                reachable = await self._check_port(host_part or '127.0.0.1', port)
            except (ValueError, TypeError):
                reachable = False
            return {
                'url': url,
                'matched_mapping': matched,
                'resolved_target': f'http://{matched.target}{parsed.path}',
                'target_reachable': reachable,
                'route': f'browser -> gost -> nginx(:{matched.internal_port}) -> {matched.target}',
            }

        exact_routes = [m for m in mappings if m.source_host == hostname]
        if self._should_generate_origin_fallback(hostname, exact_routes):
            listen_port = (
                NGINX_PROXY_MAP_HTTPS_PORT if scheme == 'https' else NGINX_PROXY_MAP_PORT
            )
            resolved_path = parsed.path or '/'
            if parsed.query:
                resolved_path = f'{resolved_path}?{parsed.query}'
            return {
                'url': url,
                'matched_mapping': None,
                'resolved_target': f'{scheme}://{hostname}{resolved_path}',
                'target_reachable': False,
                'route': f'browser -> gost -> nginx(:{listen_port}) -> {scheme}://{hostname}',
            }

        bypasses = await asyncio.to_thread(self._load_bypass_file)
        for bp in bypasses:
            if self._host_matches(hostname, bp):
                return {
                    'url': url,
                    'matched_mapping': None,
                    'resolved_target': None,
                    'target_reachable': False,
                    'route': f'browser -> gost -> DIRECT (bypass: {bp})',
                }

        return {
            'url': url,
            'matched_mapping': None,
            'resolved_target': None,
            'target_reachable': False,
            'route': 'browser -> gost -> upstream proxy -> internet',
        }

    # --- Health check ---

    async def health_check(self) -> ProxyHealthCheck:
        """Check GOST, nginx, and config consistency."""
        gost_alive = await self._check_gost_alive()
        nginx_alive = await asyncio.to_thread(self._check_nginx_alive)
        config_consistent, inconsistencies = await asyncio.to_thread(
            self._check_config_consistency
        )
        if not config_consistent:
            for msg in inconsistencies:
                logger.warning(f'Proxy config inconsistency: {msg}')

        healthy = gost_alive and nginx_alive and config_consistent
        return ProxyHealthCheck(
            healthy=healthy,
            gost_alive=gost_alive,
            nginx_alive=nginx_alive,
            config_consistent=config_consistent,
            inconsistencies=inconsistencies,
        )

    async def _check_gost_alive(self) -> bool:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f'{self._gost_api_url}/config', timeout=3)
                return resp.status_code == 200
        except Exception:
            return False

    @staticmethod
    def _check_nginx_alive() -> bool:
        return shutil.which('nginx') is not None and (
            Path('/run/nginx.pid').exists()
            or Path('/var/run/nginx.pid').exists()
            or Path('/tmp/nginx.pid').exists()
        )

    def _check_config_consistency(self) -> tuple[bool, list[str]]:
        """Compare domain sets across proxy-map.json, gost-hosts.txt, and nginx conf."""
        inconsistencies: list[str] = []

        # 1) Domains from proxy-map.json (source of truth)
        mappings = self._load_mappings()
        json_domains = {m.source_host for m in mappings}

        # 2) Domains from gost-hosts.txt
        hosts_domains: set[str] = set()
        if self._gost_hosts_file.exists():
            for line in self._gost_hosts_file.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith('#'):
                    parts = line.split()
                    if len(parts) >= 2:
                        host = parts[1]
                        # Normalize: .example.com → *.example.com for comparison
                        if host.startswith('.'):
                            host = '*' + host
                        hosts_domains.add(host)

        # 3) Domains from nginx conf (server_name directives)
        nginx_domains: set[str] = set()
        if self._nginx_conf.exists():
            for match in re.finditer(
                r'server_name\s+([^;]+);', self._nginx_conf.read_text()
            ):
                nginx_domains.add(match.group(1).strip())

        # Compare
        if json_domains and json_domains != hosts_domains:
            only_json = json_domains - hosts_domains
            only_hosts = hosts_domains - json_domains
            if only_json:
                inconsistencies.append(
                    f'In proxy-map.json but missing from gost-hosts: {only_json}'
                )
            if only_hosts:
                inconsistencies.append(
                    f'In gost-hosts but missing from proxy-map.json: {only_hosts}'
                )

        if json_domains and json_domains != nginx_domains:
            only_json = json_domains - nginx_domains
            only_nginx = nginx_domains - json_domains
            if only_json:
                inconsistencies.append(
                    f'In proxy-map.json but missing from nginx conf: {only_json}'
                )
            if only_nginx:
                inconsistencies.append(
                    f'In nginx conf but missing from proxy-map.json: {only_nginx}'
                )

        return len(inconsistencies) == 0, inconsistencies

    # --- Upstream proxy ---

    @staticmethod
    def parse_proxy_server(server: str) -> tuple[str, str, str]:
        """Parse user:pass@host:port into (addr, username, password)."""
        server = server.strip().strip('"').strip("'")
        for prefix in ('http://', 'https://'):
            if server.startswith(prefix):
                server = server[len(prefix):]
        if '@' in server:
            auth_part, addr = server.rsplit('@', 1)
            if ':' in auth_part:
                username, password = auth_part.split(':', 1)
                if username:
                    return addr, username, password
            elif auth_part:
                # username-only (no colon), e.g. user@host — maps to empty password
                return addr, auth_part, ''
            return addr, '', ''
        return server, '', ''

    async def get_upstream(self) -> ProxyUpstreamInfo | None:
        """Get current upstream proxy config from GOST API."""
        try:
            async with httpx.AsyncClient() as client:
                # GOST v3 rc10: GET /config returns the full config;
                # per-chain GET is not supported.
                resp = await client.get(
                    f'{self._gost_api_url}/config',
                    timeout=5,
                )
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                config = resp.json()
                chains = config.get('chains', [])
                chain = next((c for c in chains if c.get('name') == 'upstream'), None)
                if not chain:
                    return None
                hops = chain.get('hops', [])
                if not hops:
                    return None
                nodes = hops[0].get('nodes', [])
                if not nodes:
                    return None
                node = nodes[0]
                addr = node.get('addr', '')
                auth = node.get('connector', {}).get('auth', {})
                return ProxyUpstreamInfo(
                    addr=addr,
                    username=auth.get('username'),
                    password=auth.get('password'),
                )
        except Exception as e:
            logger.warning(f'Failed to get upstream from GOST API: {e}')
            return None

    async def _exec_auth_cmd(self, cmd: str, timeout: float = 30.0) -> str:
        """Execute an auth command via bash and return its stdout (stripped).

        Uses ``bash -c`` explicitly so that ``echo -n``, ``$(...)`` and
        ``${VAR}`` work reliably (``/bin/sh`` on some systems lacks ``-n``).
        """
        proc = await asyncio.create_subprocess_exec(
            'bash', '-c', cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f'auth_cmd timed out after {timeout}s')
        if proc.returncode != 0:
            raise RuntimeError(
                f'auth_cmd failed (exit {proc.returncode}): {stderr.decode().strip()}'
            )
        return stdout.decode().strip()

    async def set_upstream(self, server: str, auth_cmd: str | None = None) -> ProxyUpstreamInfo:
        """Update upstream proxy via GOST API. Accepts user:pass@host:port format.

        If auth_cmd is provided, it is executed as a shell command. Its stdout
        should be ``user:pass``. The result is injected into the server URL,
        replacing any inline credentials.
        """
        if auth_cmd:
            auth = await self._exec_auth_cmd(auth_cmd)
            # Extract pure host:port from server (strip any existing auth)
            addr_only, _, _ = self.parse_proxy_server(server)
            # Normalize user-only credentials to user:@host format
            if ':' not in auth:
                auth = f'{auth}:'
            server = f'{auth}@{addr_only}'

        addr, username, password = self.parse_proxy_server(server)

        connector: dict = {'type': 'http'}
        if username:
            connector['auth'] = {'username': username, 'password': password}

        hop_config = {
            'name': 'hop-0',
            'bypass': 'proxy-bypass',
            'nodes': [
                {
                    'name': 'upstream-proxy',
                    'addr': addr,
                    'connector': connector,
                    'dialer': {'type': 'tcp'},
                }
            ],
        }

        chain_config = {
            'name': 'upstream',
            'hops': [hop_config],
        }

        async with httpx.AsyncClient() as client:
            # GOST v3 rc10: PUT /config/chains/{name} to update,
            # POST /config/chains to create.
            resp = await client.put(
                f'{self._gost_api_url}/config/chains/upstream',
                json=chain_config,
                timeout=5,
            )
            if resp.status_code in (400, 404):
                # Chain doesn't exist (was in direct mode), create it
                resp = await client.post(
                    f'{self._gost_api_url}/config/chains',
                    json=chain_config,
                    timeout=5,
                )
            resp.raise_for_status()

            # Ensure the browser-proxy service references the upstream chain.
            # When PROXY_SERVER=true (no upstream at boot), the service handler
            # lacks `chain: upstream`, so traffic goes direct even though the
            # chain exists.  We fix this by updating the service config.
            await self._ensure_service_chain(client, chain_name='upstream')

        logger.info(f'Updated upstream proxy: addr={addr}, auth={"yes" if username else "no"}')
        return ProxyUpstreamInfo(
            addr=addr,
            username=username or None,
            password=password or None,
        )

    async def remove_upstream(self) -> bool:
        """Remove upstream proxy chain (switch to direct mode)."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.delete(
                    f'{self._gost_api_url}/config/chains/upstream',
                    timeout=5,
                )
                if resp.status_code == 404:
                    return False
                resp.raise_for_status()
                # Remove chain reference from the service handler
                await self._ensure_service_chain(client, chain_name=None)
            logger.info('Removed upstream proxy chain (switched to direct mode)')
            return True
        except Exception as e:
            logger.warning(f'Failed to remove upstream from GOST API: {e}')
            return False

    async def _ensure_service_chain(
        self, client: httpx.AsyncClient, *, chain_name: str | None
    ) -> None:
        """Ensure the browser-proxy service handler references (or clears) the chain.

        GOST v3 API: PUT /config/services/browser-proxy updates the service.
        We fetch the current service config, set or remove ``handler.chain``,
        and PUT it back.
        """
        try:
            resp = await client.get(f'{self._gost_api_url}/config', timeout=5)
            resp.raise_for_status()
            config = resp.json()

            services = config.get('services', [])
            svc = next((s for s in services if s.get('name') == 'browser-proxy'), None)
            if svc is None:
                logger.warning('browser-proxy service not found in GOST config')
                return

            handler = svc.get('handler', {})
            current_chain = handler.get('chain')

            if chain_name and current_chain == chain_name:
                return  # already correct
            if not chain_name and not current_chain:
                return  # already cleared

            if chain_name:
                handler['chain'] = chain_name
            else:
                handler.pop('chain', None)
            svc['handler'] = handler

            resp = await client.put(
                f'{self._gost_api_url}/config/services/browser-proxy',
                json=svc,
                timeout=5,
            )
            resp.raise_for_status()
            action = f'set chain={chain_name}' if chain_name else 'cleared chain'
            logger.info(f'Updated browser-proxy service handler: {action}')
        except Exception as e:
            logger.warning(f'Failed to update browser-proxy service handler: {e}')

    # --- Auth file watcher ---

    def start_auth_watcher(
        self,
        watch_files: list[str],
        auth_cmd: str,
        server: str,
        interval: float = 30.0,
    ) -> None:
        """Start a background task that watches files for changes.

        When any watched file's mtime changes, *auth_cmd* is re-executed and
        the upstream proxy is updated with fresh credentials.

        Args:
            watch_files: File paths to watch (e.g. ``[$SEC_TOKEN_PATH]``).
            auth_cmd: Shell command whose stdout is ``username:password``.
            server: Upstream proxy address (``host:port``).
            interval: Polling interval in seconds.
        """
        if self._auth_watcher_task and not self._auth_watcher_task.done():
            self._auth_watcher_task.cancel()
        self._auth_watcher_task = asyncio.create_task(
            self._auth_watch_loop(watch_files, auth_cmd, server, interval)
        )
        logger.info(
            f'Auth watcher started: files={watch_files}, interval={interval}s'
        )

    def stop_auth_watcher(self) -> None:
        """Stop the background auth file watcher."""
        if self._auth_watcher_task and not self._auth_watcher_task.done():
            self._auth_watcher_task.cancel()
            logger.info('Auth watcher stopped')

    async def _auth_watch_loop(
        self,
        watch_files: list[str],
        auth_cmd: str,
        server: str,
        interval: float,
    ) -> None:
        """Poll watched files and refresh upstream auth on change."""
        mtimes: dict[str, float] = {}
        # Record initial mtimes
        for f in watch_files:
            try:
                mtimes[f] = os.path.getmtime(f)
            except OSError:
                mtimes[f] = 0.0

        while True:
            try:
                await asyncio.sleep(interval)
                changed = False
                for f in watch_files:
                    try:
                        new_mtime = os.path.getmtime(f)
                    except OSError:
                        continue
                    if new_mtime != mtimes.get(f):
                        logger.info(f'Auth watch: file changed: {f}')
                        mtimes[f] = new_mtime
                        changed = True

                if changed:
                    try:
                        await self.set_upstream(server, auth_cmd=auth_cmd)
                        logger.info('Auth watch: upstream proxy refreshed')
                    except Exception as e:
                        logger.warning(f'Auth watch: failed to refresh upstream: {e}')
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f'Auth watch loop error: {e}')

    # --- Internal ---

    async def _apply(self, mappings: list[ProxyMappingRoute]):
        await asyncio.to_thread(self._save_mappings, mappings)
        # Order matters: nginx must be ready BEFORE GOST starts routing traffic.
        # 1) Write nginx conf + reload → nginx can handle the new domains
        # 2) Write GOST hosts/bypass → GOST resolves domains to 127.0.0.1 (auto-reload ~3s)
        # This eliminates the window where GOST routes to nginx before nginx is ready.
        await asyncio.to_thread(self._regenerate_nginx_conf, mappings)
        await self._reload_nginx()
        await asyncio.to_thread(self._regenerate_gost_hosts, mappings)
        await asyncio.to_thread(self._regenerate_bypass_file, mappings)

    def _extract_host(self, source: str) -> str:
        s = source
        if '://' in s:
            s = s.split('://', 1)[1]
        s = s.split('/')[0]  # strip path
        s = s.split(':')[0]  # strip port
        return s

    def _extract_path(self, source: str) -> str:
        s = source
        if '://' in s:
            s = s.split('://', 1)[1]
        # Remove host[:port] part
        if '/' in s:
            path = '/' + s.split('/', 1)[1]
            path = path.rstrip('*')  # strip trailing wildcard (visual hint only)
            if path != '/' and not path.endswith('/'):
                path += '/'
            return path
        return '/'

    def _host_matches(self, hostname: str, pattern: str) -> bool:
        if pattern.startswith('*.'):
            suffix = pattern[1:]  # .example.com
            return hostname == pattern[2:] or hostname.endswith(suffix)
        if pattern.startswith('.'):
            return hostname == pattern[1:] or hostname.endswith(pattern)
        return hostname == pattern

    def _load_mappings(self) -> list[ProxyMappingRoute]:
        if not self._proxy_map_json.exists():
            return []
        try:
            data = json.loads(self._proxy_map_json.read_text())
            return [ProxyMappingRoute(**item) for item in data]
        except Exception as e:
            logger.warning(f'Failed to load proxy mappings: {e}')
            return []

    def _save_mappings(self, mappings: list[ProxyMappingRoute]):
        self._proxy_map_json.write_text(
            json.dumps([m.model_dump() for m in mappings], indent=2)
        )

    def _load_bypass_file(self) -> list[str]:
        if not self._gost_bypass_file.exists():
            return []
        return [
            line.strip()
            for line in self._gost_bypass_file.read_text().splitlines()
            if line.strip()
        ]

    def _save_bypass_file(self, patterns: list[str]):
        self._gost_bypass_file.write_text(
            '\n'.join(patterns) + '\n' if patterns else ''
        )

    def _is_wildcard_host(self, host: str) -> bool:
        return host.startswith('*.') or host.startswith('.')

    def _should_generate_origin_fallback(
        self,
        domain: str,
        routes: list[ProxyMappingRoute],
    ) -> bool:
        if not routes:
            return False
        if self._is_wildcard_host(domain):
            return False
        return all(route.source_path != '/' for route in routes)

    def _split_target(self, target: str) -> tuple[str, str]:
        target_host_port = target.split('/')[0]
        target_path = ''
        if '/' in target.split(':', 1)[-1]:
            parts = target.split('/', 1)
            target_host_port = parts[0]
            target_path = '/' + parts[1]
        return target_host_port, target_path

    def _build_proxy_location(
        self,
        source_path: str,
        upstream: str,
        *,
        host_header: str = '$host',
        enable_sni: bool = False,
    ) -> str:
        lines = [
            f'        location {source_path} {{',
            f'            proxy_pass {upstream};',
            f'            proxy_set_header Host {host_header};',
            '            proxy_set_header X-Real-IP $remote_addr;',
            '            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;',
            '            proxy_set_header Upgrade $http_upgrade;',
            '            proxy_set_header Connection $connection_upgrade;',
            '            proxy_http_version 1.1;',
            '            proxy_read_timeout 600s;',
            '            proxy_send_timeout 600s;',
        ]
        if enable_sni:
            lines.append('            proxy_ssl_server_name on;')
        lines.append('        }')
        return '\n'.join(lines)

    def _build_server_block(
        self,
        domain: str,
        locations: list[str],
        *,
        https: bool = False,
    ) -> str:
        listen_lines = [f'    listen 127.0.0.1:{NGINX_PROXY_MAP_PORT};']
        if https:
            listen_lines = [f'    listen 127.0.0.1:{NGINX_PROXY_MAP_HTTPS_PORT} ssl;']
            listen_lines.extend(
                [
                    f'    ssl_certificate {self._tls_crt};',
                    f'    ssl_certificate_key {self._tls_key};',
                ]
            )

        return (
            'server {\n'
            + '\n'.join(listen_lines)
            + '\n'
            + f'    server_name {domain};\n'
            + '    client_max_body_size 0;\n'
            + '\n'.join(locations)
            + '\n}'
        )

    def _regenerate_nginx_conf(self, mappings: list[ProxyMappingRoute]):
        # Group by domain (source_host -> list of routes)
        domain_groups: dict[str, list[ProxyMappingRoute]] = {}
        for m in mappings:
            domain_groups.setdefault(m.source_host, []).append(m)

        blocks = []
        for domain, routes in domain_groups.items():
            routes = sorted(routes, key=lambda route: len(route.source_path), reverse=True)
            mapped_locations = []
            for r in routes:
                target_host_port, target_path = self._split_target(r.target)
                mapped_locations.append(
                    self._build_proxy_location(
                        r.source_path,
                        f'http://{target_host_port}{target_path}',
                    )
                )

            if self._should_generate_origin_fallback(domain, routes):
                http_locations = mapped_locations + [
                    self._build_proxy_location('/', f'http://{domain}')
                ]
                blocks.append(self._build_server_block(domain, http_locations))
                if self._tls_crt.exists() and self._tls_key.exists():
                    https_locations = mapped_locations + [
                        self._build_proxy_location(
                            '/',
                            f'https://{domain}',
                            enable_sni=True,
                        )
                    ]
                    blocks.append(
                        self._build_server_block(domain, https_locations, https=True)
                    )
                continue

            # Keep current behavior for root mappings and wildcard hosts.
            ssl_block = ''
            if self._tls_crt.exists() and self._tls_key.exists():
                ssl_block = (
                    f'    listen 127.0.0.1:{NGINX_PROXY_MAP_HTTPS_PORT} ssl;\n'
                    f'    ssl_certificate {self._tls_crt};\n'
                    f'    ssl_certificate_key {self._tls_key};\n'
                )

            # nginx server_name supports wildcards like *.example.com
            block = (
                f'server {{\n'
                f'    listen 127.0.0.1:{NGINX_PROXY_MAP_PORT};\n'
                f'{ssl_block}'
                f'    server_name {domain};\n'
                f'    client_max_body_size 0;\n'
                + '\n'.join(mapped_locations)
                + '\n}'
            )
            blocks.append(block)

        self._nginx_conf.write_text('\n'.join(blocks) + '\n' if blocks else '')

    def _regenerate_gost_hosts(self, mappings: list[ProxyMappingRoute]):
        # One entry per unique domain: GOST hosts format is "IP HOSTNAME"
        # GOST uses ".example.com" for wildcards (not "*.example.com")
        seen: set[str] = set()
        lines: list[str] = []
        for m in mappings:
            if m.source_host not in seen:
                seen.add(m.source_host)
                host = m.source_host
                if host.startswith('*.'):
                    host = host[1:]  # *.example.com -> .example.com
                lines.append(f'127.0.0.1 {host}')

        self._gost_hosts_file.write_text(
            '\n'.join(lines) + '\n' if lines else ''
        )
        # GOST auto-reloads this file every 3s — no restart needed

    def _load_user_bypasses(self) -> list[str]:
        if not self._user_bypass_json.exists():
            return []
        try:
            return json.loads(self._user_bypass_json.read_text())
        except Exception:
            return []

    def _save_user_bypasses(self, patterns: list[str]):
        self._user_bypass_json.write_text(json.dumps(patterns))

    def _regenerate_bypass_file(self, mappings: list[ProxyMappingRoute]):
        """Regenerate bypass file from mappings + user patterns."""
        entries: list[str] = []
        # Auto-generated from mappings
        seen: set[str] = set()
        for m in mappings:
            if m.source_host not in seen:
                entries.append(m.source_host)
                seen.add(m.source_host)
        # 127.0.0.1 is critical — hop-level bypass matches resolved IPs
        if mappings:
            entries.append('127.0.0.1')
        # User-added patterns
        user_patterns = self._load_user_bypasses()
        for p in user_patterns:
            if p not in seen:
                entries.append(p)
                seen.add(p)
        self._save_bypass_file(entries)

    async def _reload_nginx(self):
        # Debounce: cancel any pending reload and schedule a new one.
        # This ensures rapid successive changes (e.g. two DELETEs) only
        # trigger a single nginx reload after the last change, avoiding
        # dropped connections from reloading mid-request.
        if self._reload_task and not self._reload_task.done():
            self._reload_task.cancel()
        self._reload_task = asyncio.create_task(self._do_reload_nginx())

    async def _do_reload_nginx(self):
        await asyncio.sleep(NGINX_RELOAD_DEBOUNCE_SECONDS)
        proc = await asyncio.create_subprocess_exec(
            'sudo',
            'nginx',
            '-s',
            'reload',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning(f'nginx reload failed: {stderr.decode()}')
        else:
            logger.info('Reloaded nginx for proxy mapping update')

    async def _check_port(self, host: str, port: int) -> bool:
        def _check():
            try:
                with socket.create_connection((host, port), timeout=2):
                    return True
            except OSError:
                return False

        return await asyncio.to_thread(_check)
