"""Pure Python protobuf serialization for ttlogagent protocol.

Binary-compatible with the original Cython implementation (proto.pyx / protov3.pyx).
Supports both V1 and V3 wire formats.
"""

from __future__ import annotations


def _encode_varint(v: int) -> bytes:
    parts = bytearray()
    while v >= 0x80:
        parts.append((v & 0x7F) | 0x80)
        v >>= 7
    parts.append(v)
    return bytes(parts)


def _sov(x: int) -> int:
    n = 0
    while True:
        n += 1
        x >>= 7
        if x == 0:
            break
    return n


def _encode_bytes_field(tag: int, data: bytes) -> bytes:
    if not data:
        return b""
    return bytes([tag]) + _encode_varint(len(data)) + data


def _encode_varint_field(tag: int, v: int) -> bytes:
    if v == 0:
        return b""
    return bytes([tag]) + _encode_varint(v)


def _encode_map_entry(key: bytes, value: bytes) -> bytes:
    inner = _encode_bytes_field(0x0A, key) + _encode_bytes_field(0x12, value)
    return bytes([0x12]) + _encode_varint(len(inner)) + inner


# ---------------------------------------------------------------------------
# V1 Protocol
# ---------------------------------------------------------------------------

class MsgV1:
    """V1 protocol message: msg + tags."""

    __slots__ = ("msg", "tags")

    def __init__(self, msg: bytes, tags: dict[bytes, bytes]):
        self.msg = msg
        self.tags = tags

    def marshal(self) -> bytes:
        parts = bytearray()
        if self.msg:
            parts.extend(_encode_bytes_field(0x0A, self.msg))
        for k, v in self.tags.items():
            parts.extend(_encode_map_entry(k, v))
        return bytes(parts)


# ---------------------------------------------------------------------------
# V3 Protocol
# ---------------------------------------------------------------------------

class BatchMsgHeader:
    __slots__ = (
        "task_name", "language", "cluster", "psm",
        "pod_name", "stage", "host", "idc",
        "tenant", "log_stream",
    )

    _TAGS = (0x0A, 0x12, 0x1A, 0x22, 0x2A, 0x32, 0x3A, 0x42, 0x4A, 0x52)

    def __init__(
        self,
        task_name: bytes, language: bytes, cluster: bytes, psm: bytes,
        pod_name: bytes, stage: bytes, host: bytes, idc: bytes,
        tenant: bytes = b"", log_stream: bytes = b"",
    ):
        self.task_name = task_name
        self.language = language
        self.cluster = cluster
        self.psm = psm
        self.pod_name = pod_name
        self.stage = stage
        self.host = host
        self.idc = idc
        self.tenant = tenant
        self.log_stream = log_stream

    def marshal(self) -> bytes:
        parts = bytearray()
        for tag, field in zip(self._TAGS, (
            self.task_name, self.language, self.cluster, self.psm,
            self.pod_name, self.stage, self.host, self.idc,
            self.tenant, self.log_stream,
        )):
            parts.extend(_encode_bytes_field(tag, field))
        return bytes(parts)


class MsgHeaderV3:
    __slots__ = ("level", "location", "log_id", "ts", "span_id")

    def __init__(
        self,
        level: bytes, location: bytes, log_id: bytes,
        ts: int = 0, span_id: int = 0, *, trans_id: int | None = None,
    ):
        if trans_id is not None and ts == 0:
            ts = trans_id
        self.level = level
        self.location = location
        self.log_id = log_id
        self.ts = ts
        self.span_id = span_id

    def marshal(self) -> bytes:
        parts = bytearray()
        parts.extend(_encode_bytes_field(0x0A, self.level))
        parts.extend(_encode_bytes_field(0x12, self.location))
        parts.extend(_encode_bytes_field(0x1A, self.log_id))
        parts.extend(_encode_varint_field(0x20, self.ts))
        parts.extend(_encode_varint_field(0x28, self.span_id))
        return bytes(parts)


class MsgV3:
    __slots__ = ("header", "msg", "tags")

    def __init__(self, header: MsgHeaderV3, msg: bytes, tags: dict[bytes, bytes]):
        self.header = header
        self.msg = msg
        self.tags = tags

    def marshal(self) -> bytes:
        parts = bytearray()
        header_bytes = self.header.marshal()
        parts.extend(_encode_bytes_field(0x0A, header_bytes))
        for k, v in self.tags.items():
            parts.extend(_encode_map_entry(k, v))
        if self.msg:
            parts.extend(_encode_bytes_field(0x1A, self.msg))
        return bytes(parts)


class BatchMsg:
    __slots__ = ("header", "messages")

    def __init__(self, header: BatchMsgHeader, messages: list[MsgV3]):
        self.header = header
        self.messages = messages

    def marshal(self) -> bytes:
        parts = bytearray()
        header_bytes = self.header.marshal()
        parts.extend(_encode_bytes_field(0x0A, header_bytes))
        for message in self.messages:
            msg_bytes = message.marshal()
            parts.extend(_encode_bytes_field(0x12, msg_bytes))
        return bytes(parts)
