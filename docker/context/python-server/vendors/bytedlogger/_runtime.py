"""Shared runtime metadata and official LogID helpers.

Keeps environment resolution and wire-compatible LogID generation in one place
so middleware and StreamLog handler do not drift.
"""

from __future__ import annotations

import binascii
import ipaddress
import os
import random
import time
from dataclasses import dataclass
from typing import Mapping

_LOGID_VERSION = "02"
_LOGID_IP_UNKNOWN = "0" * 32
_MAX_UNIX_MILLI = 9_999_999_999_999
_LOWER_RAND = 1 << 20
_UPPER_RAND = (1 << 24) - 1


def _get_first_env(keys: tuple[str, ...], default: str) -> str:
    for key in keys:
        value = os.environ.get(key)
        if value:
            return value
    return default


def get_runtime_psm(default: str = "unknown") -> str:
    return _get_first_env(("LOAD_SERVICE_PSM", "PSM", "TCE_PSM"), default)


def get_runtime_cluster(default: str = "default") -> str:
    return _get_first_env(("SERVICE_CLUSTER",), default)


def get_runtime_pod_name(default: str = "-") -> str:
    return _get_first_env(("MY_POD_NAME", "POD_NAME"), default)


def get_runtime_stage(default: str = "-") -> str:
    if os.environ.get("IS_TCE_DOCKER_ENV") == "1":
        return os.environ.get("TCE_STAGE", default)
    return _get_first_env(("DEPLOY_STAGE", "STAGE"), default)


def get_runtime_host(default: str = "-") -> str:
    return _get_first_env(
        (
            "MY_HOST_IP",
            "HOST_IP",
            "BYTED_HOST_IP",
            "MY_HOST_IPV6",
            "HOST_IPV6",
            "BYTED_HOST_IPV6",
        ),
        default,
    )


def get_logid_host(default: str = "127.0.0.1") -> str:
    """Resolve host for LogID generation.

    Prefer the explicit `MY_HOST_*` pair before generic fallbacks so an
    intentionally injected IPv6 does not get shadowed by broader env defaults.
    """

    return _get_first_env(
        (
            "MY_HOST_IP",
            "MY_HOST_IPV6",
            "HOST_IP",
            "HOST_IPV6",
            "BYTED_HOST_IP",
            "BYTED_HOST_IPV6",
        ),
        default,
    )


def get_runtime_idc(default: str = "-") -> str:
    return _get_first_env(("RUNTIME_IDC_NAME", "IDC_NAME"), default)


def encode_host_bytes(host: str) -> bytes:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return b""
    if isinstance(ip, ipaddress.IPv4Address):
        # Match Go net.ParseIP(): IPv4 is represented as IPv6-mapped 16-byte form.
        return (b"\x00" * 10) + b"\xff\xff" + ip.packed
    return ip.packed


def format_ip_for_logid(host: str) -> str:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return _LOGID_IP_UNKNOWN
    packed = ip.packed
    if isinstance(ip, ipaddress.IPv4Address):
        packed = (b"\x00" * 10) + b"\xff\xff" + packed
    return binascii.hexlify(packed).decode("ascii")


def generate_logid() -> str:
    """Generate a LogID compatible with code.byted.org/gopkg/logid.GenLogID()."""

    host_hex = format_ip_for_logid(get_logid_host())
    unix_milli = min(max(time.time_ns() // 1_000_000, 0), _MAX_UNIX_MILLI)
    rand_hex = f"{random.randint(_LOWER_RAND, _UPPER_RAND):06x}"
    return f"{_LOGID_VERSION}{unix_milli:013d}{host_hex}{rand_hex}"


@dataclass(frozen=True)
class RuntimeMeta:
    psm: str
    cluster: str
    pod_name: str
    stage: str
    host: str
    idc: str


def resolve_runtime_meta(overrides: Mapping[str, str] | None = None) -> RuntimeMeta:
    meta = dict(overrides or {})
    return RuntimeMeta(
        psm=meta.get("psm", get_runtime_psm()),
        cluster=meta.get("cluster", get_runtime_cluster()),
        pod_name=meta.get("pod_name", get_runtime_pod_name()),
        stage=meta.get("stage", get_runtime_stage()),
        host=meta.get("host", get_runtime_host()),
        idc=meta.get("idc", get_runtime_idc()),
    )
