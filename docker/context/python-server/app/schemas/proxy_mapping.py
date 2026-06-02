from __future__ import annotations

from pydantic import BaseModel, Field


class ProxyMappingRoute(BaseModel):
    source: str = Field(
        ...,
        description='Source pattern: [protocol://]host[:port][/path], supports wildcard * in host',
        examples=['myapp.com', 'api.myapp.com/v1/*', '*.staging.app.com'],
    )
    target: str = Field(
        ...,
        description='Target address: [host:]port[/path]. Host defaults to 127.0.0.1',
        examples=['127.0.0.1:3000', ':8081/v1/', '192.168.1.5:3000'],
    )
    source_host: str = Field(..., description='Extracted host from source (used for GOST hosts)')
    source_path: str = Field(default='/', description='Extracted path from source (used for nginx location)')
    internal_port: int = Field(..., description='Internal nginx listen port for this domain group')


class ProxyMappingAddRequest(BaseModel):
    source: str = Field(
        ...,
        description='Source pattern: [protocol://]host[:port][/path], supports wildcard *',
        examples=['myapp.com', 'api.myapp.com/v1/*', '*.staging.app.com'],
    )
    target: str = Field(
        ...,
        description='Target address: [host:]port[/path]. Host defaults to 127.0.0.1',
        examples=[':3000', '127.0.0.1:8081/v1/', '192.168.1.5:3000'],
    )


class ProxyBypassRequest(BaseModel):
    pattern: str = Field(
        ...,
        description='Bypass pattern: domain (*.example.com, .example.com) or CIDR (10.0.0.0/8)',
        examples=['*.internal.company.com', '10.0.0.0/8', '.byted.org'],
    )


class ProxyDiagnoseResult(BaseModel):
    url: str
    matched_mapping: ProxyMappingRoute | None = None
    resolved_target: str | None = None
    target_reachable: bool = False
    route: str


class ProxyUpstreamInfo(BaseModel):
    addr: str = Field(
        ...,
        description='Upstream proxy address (host:port)',
        examples=['proxy.example.com:3128'],
    )
    username: str | None = Field(
        default=None,
        description='Proxy auth username (if authenticated)',
    )
    password: str | None = Field(
        default=None,
        description='Proxy auth password (if authenticated)',
    )


class ProxyHealthCheck(BaseModel):
    healthy: bool = Field(..., description='Overall health status')
    gost_alive: bool = Field(..., description='GOST proxy process is reachable via API')
    nginx_alive: bool = Field(..., description='nginx process is running')
    config_consistent: bool = Field(
        ..., description='Domain sets in proxy-map.json, gost-hosts.txt, and nginx conf are consistent'
    )
    inconsistencies: list[str] = Field(
        default_factory=list,
        description='List of inconsistency details (empty when config_consistent is true)',
    )


class ProxyUpstreamUpdateRequest(BaseModel):
    server: str = Field(
        ...,
        description='Upstream proxy server. Supports plain host:port or user:pass@host:port',
        examples=['proxy.example.com:3128', 'user:token@proxy.example.com:8080'],
    )
    auth_cmd: str | None = Field(
        default=None,
        description=(
            'Optional shell command to obtain proxy credentials. '
            'The command stdout should be "username:password". '
            'When set, the result is injected into the server URL, '
            'replacing any inline credentials.'
        ),
        examples=['echo -n "ZTI_${TCE_PSM}:$(cat $SEC_TOKEN_PATH)"', '/opt/scripts/get-proxy-auth.sh'],
    )


