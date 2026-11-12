"""Live UDP protocol shared by the device template and the host service.

The wire format is versioned and deliberately small so the C++ sender can
build it with plain ``sendto`` and the host can parse it with ``struct``.

Packet layout (little-endian, 32-byte header)::

    magic       u32  0x52435631 ("RCV1")
    version     u8   1
    msg_type    u8   1=video 2=meta 3=hello 4=log 5=snapshot 6=stats
    flags       u16  bit0 = key frame
    seq         u32  frame / message sequence
    ts_ms       u64  device monotonic milliseconds
    width       u16  video or model width (context dependent)
    height      u16  video or model height
    frag_idx    u16  0-based fragment index
    frag_count  u16  fragments in this logical message
    payload_len u16  bytes in this fragment
    reserved    u16  must be 0

Control plane (host -> device, then ack) is one ASCII line each::

    RCC1 SET seq=12 confidence=0.60 nms=0.45 max_det=20
    RCC1 ACK seq=12 ok=1 applied=confidence,nms

Keeping the control plane textual avoids a JSON parser in the device demo.

Meta JSON (carried inside a ``META`` message) is deliberately open-ended; the
host forwards it verbatim to the browser. The overlay understands two sibling
arrays:

* ``dets`` — legacy boxes, normalized center ``x,y,w,h`` in model-input space.
* ``shapes`` — generic annotations (v1.1). ``annotations`` is accepted as a
  backward-compatible alias. Each shape::

      {"kind": "polyline|polygon|point|text",
       "space": "model|frame",          # default frame
       "pts": [[x,y], ...],             # normalized [0,1]
       "label": "left lane",            # optional
       "target": 0, "score": 0.91,      # optional
       "color": "#3ddc97",              # optional, browser validates
       "width": 4,                      # optional, 1080p-baseline px
       "closed": false}                 # optional; default true for polygon

``space:"frame"`` means normalized to ``frame_w × frame_h`` (already
transformed); ``space:"model"`` means normalized to the model input and the
browser applies the inverse of ``preprocess``. Keep ``len(meta) <= 8192``,
at most 16 shapes and 80 points per shape; both the host and the UI enforce
these caps.
"""

from __future__ import annotations

import json
import struct
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

MAGIC = 0x52435631
VERSION = 1
HEADER = struct.Struct("<IBBH I Q HHHHH H")
HEADER_SIZE = HEADER.size  # 32

VIDEO = 1
META = 2
HELLO = 3
LOG = 4
SNAPSHOT = 5
STATS = 6

MSG_NAMES = {
    VIDEO: "video",
    META: "meta",
    HELLO: "hello",
    LOG: "log",
    SNAPSHOT: "snapshot",
    STATS: "stats",
}

FLAG_KEYFRAME = 0x0001
MAX_PAYLOAD = 1200
DEFAULT_VIDEO_PORT = 5000
DEFAULT_CONTROL_PORT = 5001


def _header(
    msg_type: int,
    *,
    seq: int,
    ts_ms: int,
    width: int = 0,
    height: int = 0,
    frag_idx: int = 0,
    frag_count: int = 1,
    payload_len: int = 0,
    flags: int = 0,
) -> bytes:
    return HEADER.pack(
        MAGIC,
        VERSION,
        msg_type & 0xFF,
        flags & 0xFFFF,
        seq & 0xFFFFFFFF,
        ts_ms & 0xFFFFFFFFFFFFFFFF,
        width & 0xFFFF,
        height & 0xFFFF,
        frag_idx & 0xFFFF,
        frag_count & 0xFFFF,
        payload_len & 0xFFFF,
        0,
    )


def parse_header(data: bytes) -> dict[str, Any]:
    """Parse a 32-byte header. Raises ValueError for foreign packets."""
    if len(data) < HEADER_SIZE:
        raise ValueError(f"short header: {len(data)} < {HEADER_SIZE}")
    (
        magic,
        version,
        msg_type,
        flags,
        seq,
        ts_ms,
        width,
        height,
        frag_idx,
        frag_count,
        payload_len,
        _reserved,
    ) = HEADER.unpack_from(data)
    if magic != MAGIC:
        raise ValueError(f"wrong magic: {magic:#x}")
    if version != VERSION:
        raise ValueError(f"unsupported version: {version}")
    if msg_type not in MSG_NAMES:
        raise ValueError(f"unknown message type: {msg_type}")
    if frag_count == 0 or frag_idx >= frag_count:
        raise ValueError(f"bad fragmentation: {frag_idx}/{frag_count}")
    if payload_len > len(data) - HEADER_SIZE:
        raise ValueError(f"payload_len {payload_len} exceeds packet size {len(data) - HEADER_SIZE}")
    return {
        "magic": magic,
        "version": version,
        "msg_type": msg_type,
        "type": MSG_NAMES[msg_type],
        "flags": flags,
        "keyframe": bool(flags & FLAG_KEYFRAME),
        "seq": seq,
        "ts_ms": ts_ms,
        "width": width,
        "height": height,
        "frag_idx": frag_idx,
        "frag_count": frag_count,
        "payload_len": payload_len,
        "payload": data[HEADER_SIZE:HEADER_SIZE + payload_len],
    }


def fragment(
    msg_type: int,
    payload: bytes,
    *,
    seq: int,
    ts_ms: int = 0,
    width: int = 0,
    height: int = 0,
    flags: int = 0,
    max_payload: int = MAX_PAYLOAD,
) -> list[bytes]:
    """Split one logical message into packets ready for ``sendto``.

    ``max_payload`` is the UDP *payload* budget; keep it at or below the
    interface MTU minus IP/UDP headers (1200 is a safe default on Wi-Fi).
    """
    if max_payload < 64:
        raise ValueError("max_payload must be >= 64")
    payload = bytes(payload)
    if not payload:
        return [_header(msg_type, seq=seq, ts_ms=ts_ms, width=width, height=height,
                        frag_count=1, payload_len=0, flags=flags)]
    count = (len(payload) + max_payload - 1) // max_payload
    if count > 0xFFFF:
        raise ValueError("message too large to fragment")
    packets: list[bytes] = []
    for idx in range(count):
        chunk = payload[idx * max_payload:(idx + 1) * max_payload]
        packets.append(_header(
            msg_type, seq=seq, ts_ms=ts_ms, width=width, height=height,
            frag_idx=idx, frag_count=count, payload_len=len(chunk), flags=flags,
        ) + chunk)
    return packets


def encode_json(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def decode_json(payload: bytes) -> Any:
    return json.loads(payload.decode("utf-8"))


@dataclass
class CompletedMessage:
    addr: tuple[str, int]
    header: dict[str, Any]
    payload: bytes

    @property
    def msg_type(self) -> int:
        return self.header["msg_type"]

    @property
    def type(self) -> str:
        return self.header["type"]

    @property
    def seq(self) -> int:
        return self.header["seq"]

    @property
    def keyframe(self) -> bool:
        return self.header["keyframe"]


@dataclass
class ReassemblerStats:
    completed: int = 0
    dropped: int = 0
    duplicates: int = 0
    out_of_order: int = 0
    malformed: int = 0
    packets: int = 0
    bytes: int = 0


@dataclass
class _Buffer:
    header: dict[str, Any]
    fragments: dict[int, bytes] = field(default_factory=dict)
    first_seen: float = field(default_factory=time.monotonic)


class Reassembler:
    """Reassemble fragmented UDP messages, keyed by sender and sequence.

    Out-of-order fragments are accepted. A message with a missing fragment is
    dropped after ``timeout_s``; the demo is expected to wait for the next key
    frame rather than display a corrupt picture.
    """

    def __init__(self, *, timeout_s: float = 2.0, max_buffers: int = 512):
        self.timeout_s = timeout_s
        self.max_buffers = max_buffers
        self.stats = ReassemblerStats()
        self._buffers: dict[tuple[tuple[str, int], int, int], _Buffer] = {}

    def add(self, data: bytes, addr: tuple[str, int]) -> CompletedMessage | None:
        self.stats.packets += 1
        self.stats.bytes += len(data)
        try:
            header = parse_header(data)
        except ValueError:
            self.stats.malformed += 1
            return None

        key = (addr, header["seq"], header["msg_type"])
        buf = self._buffers.get(key)
        if buf is None:
            if len(self._buffers) >= self.max_buffers:
                oldest = min(self._buffers.items(), key=lambda item: item[1].first_seen)[0]
                self._buffers.pop(oldest, None)
                self.stats.dropped += 1
            buf = _Buffer(header=header)
            self._buffers[key] = buf
        elif header["frag_count"] != buf.header["frag_count"]:
            # Sequence reuse or a protocol mismatch: restart the message.
            self.stats.malformed += 1
            self._buffers.pop(key, None)
            return None

        idx = header["frag_idx"]
        if idx in buf.fragments:
            self.stats.duplicates += 1
            return None
        if buf.fragments and idx < max(buf.fragments):
            self.stats.out_of_order += 1
        buf.fragments[idx] = header["payload"]

        if len(buf.fragments) != buf.header["frag_count"]:
            return None

        self._buffers.pop(key, None)
        try:
            payload = b"".join(buf.fragments[i] for i in range(buf.header["frag_count"]))
        except KeyError:
            self.stats.dropped += 1
            return None
        if len(payload) != sum(len(buf.fragments[i]) for i in range(buf.header["frag_count"])):
            self.stats.dropped += 1
            return None
        self.stats.completed += 1
        return CompletedMessage(addr=addr, header=buf.header, payload=payload)

    def expire(self, now: float | None = None) -> int:
        now = time.monotonic() if now is None else now
        stale = [key for key, buf in self._buffers.items() if now - buf.first_seen > self.timeout_s]
        for key in stale:
            self._buffers.pop(key, None)
        self.stats.dropped += len(stale)
        return len(stale)

    @property
    def pending(self) -> int:
        return len(self._buffers)


# ---------------------------------------------------------------------------
# Control plane
# ---------------------------------------------------------------------------

def _coerce(value: str) -> Any:
    lower = value.lower()
    if lower in ("true", "yes", "on"):
        return True
    if lower in ("false", "no", "off"):
        return False
    try:
        return int(value, 0)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def format_control(command: str, seq: int, params: Iterable[tuple[str, Any]] | dict[str, Any] | None = None) -> bytes:
    parts = ["RCC1", command.upper(), f"seq={int(seq)}"]
    if params:
        items = params.items() if isinstance(params, dict) else params
        for key, value in items:
            if isinstance(value, bool):
                value = "1" if value else "0"
            parts.append(f"{key}={value}")
    return (" ".join(parts) + "\n").encode("ascii", "replace")


def parse_control(line: bytes | str) -> dict[str, Any]:
    if isinstance(line, bytes):
        line = line.decode("utf-8", "replace")
    text = line.strip()
    if not text:
        raise ValueError("empty control line")
    parts = text.split()
    if len(parts) < 2 or parts[0] != "RCC1":
        raise ValueError("not an RCC1 control line")
    result: dict[str, Any] = {"command": parts[1].upper(), "params": {}}
    for item in parts[2:]:
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        result["params"][key] = _coerce(value)
    return result


# ---------------------------------------------------------------------------
# Coordinate mapping
# ---------------------------------------------------------------------------

def transform_point(
    x: float,
    y: float,
    *,
    space: str = "frame",
    frame_w: int,
    frame_h: int,
    model_w: int,
    model_h: int,
    preprocess: str = "stretch",
    pad_x: float = 0.0,
    pad_y: float = 0.0,
    scale: float = 1.0,
) -> dict[str, float]:
    """Map a normalized annotation point to frame pixels.

    ``space="frame"`` points are already normalized to the frame, so they map
    directly. ``space="model"`` points are normalized to the model input and
    the inverse of ``preprocess`` is applied, matching ``transform_box``.
    """
    x = float(x)
    y = float(y)
    if space != "model":
        return {"x": x * frame_w, "y": y * frame_h}
    if preprocess == "letterbox":
        scale = scale or 1.0
        return {"x": (x * model_w - pad_x) / scale,
                "y": (y * model_h - pad_y) / scale}
    if preprocess == "crop":
        scale = scale or 1.0
        crop_w = model_w / scale
        crop_h = model_h / scale
        return {"x": (frame_w - crop_w) / 2.0 + x * crop_w,
                "y": (frame_h - crop_h) / 2.0 + y * crop_h}
    return {"x": x * frame_w, "y": y * frame_h}


def transform_box(
    det: dict[str, Any],
    *,
    frame_w: int,
    frame_h: int,
    model_w: int,
    model_h: int,
    preprocess: str = "stretch",
    pad_x: float = 0.0,
    pad_y: float = 0.0,
    scale: float = 1.0,
) -> dict[str, float]:
    """Map a normalized center-based detection to frame pixels.

    ``stretch`` means the model input was resized to the full frame, so
    normalized coordinates map directly. ``letterbox`` means the model input
    was scaled by ``scale`` and padded by ``pad_x``/``pad_y``; the inverse is
    applied here. ``crop`` maps the normalized coordinates to a centered crop
    whose size is ``model_w/scale`` by ``model_h/scale``.
    """
    cx = float(det.get("x", 0.0))
    cy = float(det.get("y", 0.0))
    bw = float(det.get("w", 0.0))
    bh = float(det.get("h", 0.0))

    if preprocess == "letterbox":
        scale = scale or 1.0
        fx = ((cx * model_w) - pad_x) / scale
        fy = ((cy * model_h) - pad_y) / scale
        fw = (bw * model_w) / scale
        fh = (bh * model_h) / scale
    elif preprocess == "crop":
        scale = scale or 1.0
        crop_w = model_w / scale
        crop_h = model_h / scale
        off_x = (frame_w - crop_w) / 2.0
        off_y = (frame_h - crop_h) / 2.0
        fx = off_x + cx * crop_w
        fy = off_y + cy * crop_h
        fw = bw * crop_w
        fh = bh * crop_h
    else:
        fx = cx * frame_w
        fy = cy * frame_h
        fw = bw * frame_w
        fh = bh * frame_h

    return {
        "x1": max(0.0, fx - fw / 2.0),
        "y1": max(0.0, fy - fh / 2.0),
        "x2": min(float(frame_w), fx + fw / 2.0),
        "y2": min(float(frame_h), fy + fh / 2.0),
        "cx": fx,
        "cy": fy,
        "w": fw,
        "h": fh,
    }


def h264_contains_idr(data: bytes) -> bool:
    """Return True when an Annex-B access unit contains an IDR NAL (type 5)."""
    for start, sc_len in _annexb_nal_starts(data):
        if start + sc_len >= len(data):
            continue
        if data[start + sc_len] & 0x1F == 5:
            return True
    return False


def h264_contains_parameter_set(data: bytes) -> bool:
    """Return True when an Annex-B access unit contains SPS (7) or PPS (8)."""
    for start, sc_len in _annexb_nal_starts(data):
        if start + sc_len >= len(data):
            continue
        if data[start + sc_len] & 0x1F in (7, 8):
            return True
    return False


def _annexb_nal_starts(data: bytes) -> Iterable[tuple[int, int]]:
    i = 0
    n = len(data)
    while i + 3 <= n:
        if data[i:i + 3] == b"\x00\x00\x01":
            yield i, 3
            i += 3
        elif i + 4 <= n and data[i:i + 4] == b"\x00\x00\x00\x01":
            yield i, 4
            i += 4
        else:
            i += 1