#!/usr/bin/env python3
"""Host-side live viewer for reCamera 2002 demos.

The device runs a plain C/C++ demo (no Web UI, no Node-RED, no Supervisor) and
pushes H.264 video plus detection JSON over UDP. This service receives it,
serves a fixed browser UI, streams the video to the browser, and forwards
parameter changes back to the demo.

Design constraints:

* Python standard library only (the browser player is a vendored MIT library).
* Works on Linux, macOS and Windows. No fork/setsid, no POSIX-only APIs.
* The device is never asked to run an HTTP server or a Web UI.
* Live viewing is **not** evidence. A session directory is written so
  ``capture_evidence.py --source live`` can turn it into normal evidence.

Typical use::

    python3 scripts/serve_demo.py --demo-dir ~/reCamera_demo/my_demo \
        --device-host 192.168.42.1 --open

    # LAN access, with a token
    python3 scripts/serve_demo.py --bind 0.0.0.0 --token secret --open
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import queue
import secrets
import shutil
import socket
import struct
import subprocess
import signal
import sys
import threading
import time
import traceback
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from demo_lib import SKILL_ROOT, now_iso, redact, sha256_bytes, write_json  # noqa: E402
from devkit.live import (  # noqa: E402
    DEFAULT_CONTROL_PORT,
    DEFAULT_VIDEO_PORT,
    HELLO,
    LOG,
    META,
    SNAPSHOT,
    STATS,
    VIDEO,
    Reassembler,
    decode_json,
    format_control,
    parse_control,
)

UI_ROOT = SKILL_ROOT / "templates" / "live-ui"
SCRIPTS = SKILL_ROOT / "scripts"
TOKEN_HEADER = "X-Live-Token"
MAX_BODY = 1 << 20
META_MAX_BYTES = 8192
MAX_SHAPES = 16
MAX_SHAPE_POINTS = 80


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def _local_ipv4() -> str:
    """Best-effort LAN address; UDP connect sends no packets."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _local_ip_for(device_host: str | None) -> str:
    """The local address the device should send UDP back to."""
    if not device_host:
        return _local_ipv4()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((device_host, 9))
            return sock.getsockname()[0]
    except OSError:
        return _local_ipv4()


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Shared live state
# ---------------------------------------------------------------------------

@dataclass
class LiveState:
    demo_name: str = ""
    started_at: str = field(default_factory=now_iso)
    hello: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None
    stats: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    param_schema: dict[str, Any] = field(default_factory=dict)
    device_addr: str = ""
    last_seen: float = 0.0
    last_video_at: float = 0.0
    last_meta_at: float = 0.0
    video_seq: int = 0
    video_frames: int = 0
    keyframes: int = 0
    video_bytes: int = 0
    meta_count: int = 0
    packets: int = 0
    packet_bytes: int = 0
    fragment_drops: int = 0
    logs: list[dict[str, Any]] = field(default_factory=list)
    log_limit: int = 500
    session_dir: str = ""
    session_ok: bool = True
    implemented: bool = False
    perf: dict[str, Any] = field(default_factory=dict)
    meta_age_samples: list[tuple[float, float]] = field(default_factory=list)
    meta_times: list[float] = field(default_factory=list)
    ingest_error: str = ""
    reassembler_stats: dict[str, Any] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def update_hello(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self.hello = payload
            if not self.demo_name and payload.get("demo"):
                self.demo_name = str(payload["demo"])
            if payload.get("params") and not self.params:
                self.params = dict(payload["params"])
            if payload.get("params_schema"):
                # demo.json wins for overlapping keys, but the device can
                # advertise controls the manifest did not repeat.
                merged = dict(payload["params_schema"])
                merged.update(self.param_schema)
                self.param_schema = merged

    def update_meta(self, payload: dict[str, Any]) -> None:
        now = time.monotonic()
        with self._lock:
            self.meta = payload
            self.meta_count += 1
            self.last_meta_at = now
            age = payload.get("age_ms")
            if isinstance(age, (int, float)) and age >= 0:
                self.meta_age_samples.append((now, float(age)))
            self.meta_times.append(now)
            cutoff = now - 10.0
            while self.meta_times and self.meta_times[0] < cutoff:
                self.meta_times.pop(0)
            while self.meta_age_samples and self.meta_age_samples[0][0] < cutoff:
                self.meta_age_samples.pop(0)
            del self.meta_times[:-600]
            del self.meta_age_samples[:-600]
            params = payload.get("params")
            if isinstance(params, dict):
                self.params.update(params)

    def update_stats(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self.stats.update(payload)
            self.last_seen = time.monotonic()

    def add_log(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self.logs.append(entry)
            if len(self.logs) > self.log_limit:
                del self.logs[: len(self.logs) - self.log_limit]

    def note_video(self, header: dict[str, Any], size: int) -> None:
        now = time.monotonic()
        with self._lock:
            self.video_seq = int(header.get("seq", 0))
            self.video_frames += 1
            self.video_bytes += size
            self.last_video_at = now
            self.last_seen = now
            if header.get("keyframe"):
                self.keyframes += 1

    def note_packet(self, size: int) -> None:
        with self._lock:
            self.packets += 1
            self.packet_bytes += size

    def touch(self) -> None:
        with self._lock:
            self.last_seen = time.monotonic()

    def set_params(self, values: dict[str, Any]) -> None:
        with self._lock:
            self.params.update(values)

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            video_age = now - self.last_video_at if self.last_video_at else None
            meta_age = now - self.last_meta_at if self.last_meta_at else None
            return {
                "ok": True,
                "demo": self.demo_name,
                "started_at": self.started_at,
                "device_addr": self.device_addr,
                "connected": (now - self.last_seen) < 3.0 if self.last_seen else False,
                "hello": self.hello,
                "meta": self.meta,
                "meta_count": self.meta_count,
                "stats": dict(self.stats),
                "params": dict(self.params),
                "param_schema": dict(self.param_schema),
                "video": {
                    "frames": self.video_frames,
                    "keyframes": self.keyframes,
                    "bytes": self.video_bytes,
                    "last_seq": self.video_seq,
                    "age_s": round(video_age, 3) if video_age is not None else None,
                    "fps": self._fps_locked(),
                },
                "meta_age_s": round(meta_age, 3) if meta_age is not None else None,
                "pipeline": self._pipeline_locked(now),
                "packets": self.packets,
                "packet_bytes": self.packet_bytes,
                "fragment_drops": self.fragment_drops,
                "logs": self.logs[-100:],
                "session_dir": self.session_dir,
                "session_ok": self.session_ok,
                "implemented": self.implemented,
                "implemented_observed": bool(self.video_frames > 0 or self.meta_count > 0),
                "ingest_error": self.ingest_error,
                "reassembler": dict(self.reassembler_stats),
            }

    def _pipeline_locked(self, now: float) -> dict[str, Any]:
        """Measured result age / update rate versus the demo's live.perf gate.

        ``age_ms`` comes from the device (queue + VPSS + inference), so it does
        not depend on host/device clock alignment.  ``ok`` stays None until
        there are enough samples to judge; a missing live.perf is not enforced.
        """
        ages = sorted(age for _, age in self.meta_age_samples)
        samples = len(ages)
        age_median = ages[samples // 2] if samples else None
        age_p90 = ages[min(samples - 1, int(samples * 0.9))] if samples else None
        detect_fps = 0.0
        if len(self.meta_times) >= 2:
            span = self.meta_times[-1] - self.meta_times[0]
            if span > 0:
                detect_fps = (len(self.meta_times) - 1) / span
        video_fps_value = self.stats.get("fps") if isinstance(self.stats, dict) else None
        video_fps = (float(video_fps_value)
                     if isinstance(video_fps_value, (int, float)) and video_fps_value > 0
                     else None)

        thresholds = dict(self.perf or {})
        checks: list[dict[str, Any]] = []

        def add(name: str, value: Any, limit: Any, ok: bool) -> None:
            checks.append({"name": name, "value": value, "limit": limit, "ok": bool(ok)})

        max_age = thresholds.get("max_result_age_ms")
        if isinstance(max_age, (int, float)):
            add("result_age_ms",
                round(age_median, 1) if age_median is not None else None,
                max_age, age_median is not None and age_median <= max_age)
        min_detect = thresholds.get("min_detect_fps")
        if isinstance(min_detect, (int, float)):
            add("detect_fps", round(detect_fps, 2), min_detect, detect_fps >= min_detect)
        min_video = thresholds.get("min_video_fps")
        if isinstance(min_video, (int, float)) and video_fps is not None:
            add("video_fps", round(video_fps, 2), min_video, video_fps >= min_video)

        enforced = bool(thresholds)
        measured = samples >= 10 and len(self.meta_times) >= 2
        ok = None
        if enforced and measured:
            ok = all(check["ok"] for check in checks)
        return {
            "samples": samples,
            "result_age_ms": round(age_median, 1) if age_median is not None else None,
            "result_age_p90_ms": round(age_p90, 1) if age_p90 is not None else None,
            "detect_fps": round(detect_fps, 2),
            "video_fps": round(video_fps, 2) if video_fps is not None else None,
            "thresholds": thresholds,
            "enforced": enforced,
            "measured": measured,
            "ok": ok,
            "checks": checks,
        }

    def _fps_locked(self) -> float:
        # The device reports fps in stats/meta; fall back to a coarse estimate.
        for source in (self.stats, self.meta or {}):
            value = source.get("fps") if isinstance(source, dict) else None
            if isinstance(value, (int, float)) and value > 0:
                return round(float(value), 2)
        return 0.0


# ---------------------------------------------------------------------------
# Fan-out hubs
# ---------------------------------------------------------------------------

class EventHub:
    def __init__(self, maxsize: int = 256):
        self.maxsize = maxsize
        self._clients: set[queue.Queue] = set()
        self._lock = threading.Lock()

    def register(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=self.maxsize)
        with self._lock:
            self._clients.add(q)
        return q

    def unregister(self, q: queue.Queue) -> None:
        with self._lock:
            self._clients.discard(q)

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait(event)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except (queue.Empty, queue.Full):
                    pass


@dataclass
class VideoClient:
    queue: queue.Queue
    need_keyframe: bool = True


class VideoHub:
    def __init__(self, maxsize: int = 90):
        self.maxsize = maxsize
        self._clients: list[VideoClient] = []
        self._lock = threading.Lock()

    def register(self) -> VideoClient:
        client = VideoClient(queue=queue.Queue(maxsize=self.maxsize))
        with self._lock:
            self._clients.append(client)
        return client

    def unregister(self, client: VideoClient) -> None:
        with self._lock:
            if client in self._clients:
                self._clients.remove(client)

    def broadcast(self, ts_ms: int, data: bytes, keyframe: bool) -> None:
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            if client.need_keyframe and not keyframe:
                continue
            if keyframe:
                client.need_keyframe = False
            item = (ts_ms, data)
            try:
                client.queue.put_nowait(item)
            except queue.Full:
                try:
                    client.queue.get_nowait()
                    client.queue.put_nowait(item)
                except (queue.Empty, queue.Full):
                    client.need_keyframe = True


# ---------------------------------------------------------------------------
# Session recording
# ---------------------------------------------------------------------------

class SessionRecorder:
    """Write a reproducible session directory.

    Small events are buffered in memory and flushed by a writer thread; video
    recording is opt-in because a 1080p30 H.264 stream is ~1 MB/s.
    """

    def __init__(self, session_dir: Path, *, demo_name: str, record_video: bool = False):
        self.dir = session_dir
        self.demo_name = demo_name
        self.record_video = record_video
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "frames").mkdir(exist_ok=True)
        (self.dir / "logs").mkdir(exist_ok=True)
        self.log_path = self.dir / "logs" / "device.log"
        self.events_path = self.dir / "events.jsonl"
        self.events_path.touch()
        self.video_path = self.dir / "video" / "stream.h264"
        if record_video:
            self.video_path.parent.mkdir(exist_ok=True)
        self._queue: queue.Queue = queue.Queue(maxsize=512)
        self._stop = threading.Event()
        self._closed = False
        self._thread = threading.Thread(target=self._writer, name="session-writer", daemon=True)
        self._thread.start()
        self._meta: dict[str, Any] = {
            "schema_version": 1,
            "kind": "demo-live-session",
            "demo": demo_name,
            "started_at": now_iso(),
            "ended_at": None,
            "video": {"codec": "h264", "frames": 0, "keyframes": 0, "bytes": 0},
            "meta": {"count": 0},
            "params": {"initial": {}, "final": {}, "changes": []},
            "logs": {"path": "logs/device.log", "lines": 0},
            "frames": [],
            "artifacts": [],
            "ok": True,
        }
        self._lock = threading.Lock()

    def _writer(self) -> None:
        while True:
            try:
                kind, value = self._queue.get(timeout=0.2)
            except queue.Empty:
                if self._stop.is_set():
                    return
                continue
            try:
                if kind == "log":
                    with self.log_path.open("a", encoding="utf-8") as fh:
                        fh.write(value + "\n")
                elif kind == "event":
                    with self.events_path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(value, ensure_ascii=False) + "\n")
                elif kind == "snapshot":
                    path, data, seq = value
                    path.write_bytes(data)
                    with self._lock:
                        self._meta["frames"].append({
                            "path": str(path.relative_to(self.dir)),
                            "seq": seq,
                            "size": len(data),
                            "sha256": sha256_bytes(data),
                        })
                elif kind == "video":
                    with self.video_path.open("ab") as fh:
                        fh.write(value)
            except OSError:
                pass
            finally:
                self._queue.task_done()

    def _put(self, kind: str, value: Any) -> None:
        try:
            self._queue.put_nowait((kind, value))
        except queue.Full:
            pass

    def log(self, line: str) -> None:
        with self._lock:
            self._meta["logs"]["lines"] += 1
        self._put("log", line.rstrip("\n"))

    def event(self, event: dict[str, Any]) -> None:
        self._put("event", event)

    def snapshot(self, data: bytes, seq: int) -> None:
        name = f"{seq:08d}.jpg"
        self._put("snapshot", (self.dir / "frames" / name, data, seq))

    def video(self, data: bytes) -> None:
        if self.record_video:
            self._put("video", data)

    def note_video(self, header: dict[str, Any]) -> None:
        with self._lock:
            self._meta["video"]["frames"] += 1
            self._meta["video"]["bytes"] += int(header.get("_payload_size", 0) or 0)
            if header.get("keyframe"):
                self._meta["video"]["keyframes"] += 1

    def note_meta(self) -> None:
        with self._lock:
            self._meta["meta"]["count"] += 1

    def set_params(self, initial: dict[str, Any] | None = None, final: dict[str, Any] | None = None,
                   change: dict[str, Any] | None = None) -> None:
        with self._lock:
            if initial is not None:
                self._meta["params"]["initial"] = dict(initial)
            if final is not None:
                self._meta["params"]["final"] = dict(final)
            if change is not None:
                self._meta["params"]["changes"].append(change)

    def set_device(self, device: dict[str, Any]) -> None:
        with self._lock:
            self._meta["device"] = device

    def close(self, *, ok: bool = True) -> Path:
        if self._closed:
            return self.dir
        self._closed = True
        self._stop.set()
        self._thread.join(timeout=5)
        with self._lock:
            self._meta["ended_at"] = now_iso()
            self._meta["ok"] = bool(ok)
            if self.log_path.is_file():
                self._meta["artifacts"].append({
                    "path": "logs/device.log",
                    "size": self.log_path.stat().st_size,
                    "sha256": sha256_bytes(self.log_path.read_bytes()),
                })
            if self.events_path.is_file():
                self._meta["artifacts"].append({
                    "path": "events.jsonl",
                    "size": self.events_path.stat().st_size,
                    "sha256": sha256_bytes(self.events_path.read_bytes()),
                })
            for item in self._meta["frames"]:
                item["sha256"] = item.get("sha256", "")
        write_json(self.dir / "session.json", self._meta)
        return self.dir


# ---------------------------------------------------------------------------
# Control plane
# ---------------------------------------------------------------------------

class ControlClient:
    def __init__(self, device_host: str | None, port: int, *, timeout_s: float = 1.0):
        self.device_host = device_host
        self.port = port
        self.timeout_s = timeout_s

    @property
    def enabled(self) -> bool:
        return bool(self.device_host)

    def send(self, params: dict[str, Any], *, command: str = "SET", seq: int | None = None,
             retries: int = 2) -> dict[str, Any]:
        if not self.device_host:
            return {"ok": False, "detail": "device-host not configured"}
        seq = int(time.time() * 1000) & 0xFFFFFFFF if seq is None else int(seq)
        line = format_control(command, seq, params)
        last_error = ""
        for _ in range(max(1, retries)):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.settimeout(self.timeout_s)
                    sock.sendto(line, (self.device_host, self.port))
                    data, _addr = sock.recvfrom(4096)
                ack = parse_control(data)
                return {
                    "ok": bool(ack.get("params", {}).get("ok", 0)),
                    "seq": ack.get("params", {}).get("seq", seq),
                    "applied": str(ack.get("params", {}).get("applied", "")).split(",")
                    if ack.get("params", {}).get("applied") else [],
                    "ignored": str(ack.get("params", {}).get("ignored", "")).split(",")
                    if ack.get("params", {}).get("ignored") else [],
                    "detail": data.decode("utf-8", "replace").strip(),
                }
            except socket.timeout:
                last_error = "no ack from device (timeout)"
            except OSError as exc:
                last_error = str(exc)
        return {"ok": False, "detail": last_error, "seq": seq}


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class LiveHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr: tuple[str, int], handler: type[BaseHTTPRequestHandler],
                 *, state: LiveState, events: EventHub, video: VideoHub,
                 recorder: SessionRecorder, config: argparse.Namespace):
        super().__init__(addr, handler)
        self.state = state
        self.events = events
        self.video = video
        self.recorder = recorder
        self.config = config
        self.token = config.token or ""
        self.action_lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    server: LiveHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep the terminal readable; the UI has a log pane.
        pass

    # ---- helpers ---------------------------------------------------------
    def _url(self) -> tuple[str, dict[str, list[str]]]:
        parsed = urlparse(self.path)
        return parsed.path, parse_qs(parsed.query)

    def _token_ok(self, query: dict[str, list[str]]) -> bool:
        expected = self.server.token
        if not expected:
            return True
        supplied = self.headers.get(TOKEN_HEADER, "") or (query.get("token", [""])[0])
        return secrets.compare_digest(
            str(supplied).encode("utf-8"), expected.encode("utf-8"))

    def _deny(self) -> None:
        self._send_json({"ok": False, "error": "unauthorized"}, status=401)

    def _security_headers(self) -> None:
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any] | None:
        ctype = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            self.close_connection = True
            return None
        length = _safe_int(self.headers.get("Content-Length"), 0)
        if length <= 0 or length > MAX_BODY:
            self.close_connection = True
            return None
        try:
            body = self.rfile.read(length)
        except OSError:
            self.close_connection = True
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.close_connection = True
            return None

    def _start_chunked(self, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Transfer-Encoding", "chunked")
        self._security_headers()
        self.end_headers()
        self.close_connection = True

    def _chunk(self, data: bytes) -> None:
        if not data:
            return
        self.wfile.write(f"{len(data):X}\r\n".encode("ascii"))
        self.wfile.write(data)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    def _end_chunked(self) -> None:
        try:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except OSError:
            pass

    # ---- GET -------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        path, query = self._url()
        if path.startswith("/api/") and path != "/api/health" and not self._token_ok(query):
            self._deny()
            return
        try:
            if path in ("/", "/index.html"):
                self._static("index.html")
            elif path == "/api/health":
                self._send_json({"ok": True, "service": "serve_demo"})
            elif path == "/api/state":
                self._send_json(self.server.state.snapshot())
            elif path == "/api/params":
                snap = self.server.state.snapshot()
                self._send_json({"ok": True, "params": snap["params"], "schema": snap["param_schema"]})
            elif path == "/api/events":
                self._stream_events()
            elif path == "/api/video":
                self._stream_video()
            elif path == "/api/session":
                self._send_json({"ok": True, "dir": self.server.recorder.dir.as_posix()})
            elif path.startswith("/vendor/") or path in ("/app.js", "/style.css", "/favicon.svg"):
                self._static(path.lstrip("/"))
            else:
                self._send_json({"ok": False, "error": "not found"}, status=404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:  # pragma: no cover - defensive
            traceback.print_exc()
            try:
                self._send_json({"ok": False, "error": "internal error"}, status=500)
            except OSError:
                pass

    def _static(self, rel: str) -> None:
        root = Path(self.server.config.ui_dir).resolve()
        target = (root / rel).resolve()
        if root not in target.parents and target != root:
            self._send_json({"ok": False, "error": "invalid path"}, status=403)
            return
        if not target.is_file():
            self._send_json({"ok": False, "error": "not found"}, status=404)
            return
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".svg": "image/svg+xml",
            ".json": "application/json; charset=utf-8",
            ".map": "application/json; charset=utf-8",
        }.get(target.suffix.lower(), "application/octet-stream")
        self._send_bytes(target.read_bytes(), ctype)

    def _stream_events(self) -> None:
        self._start_chunked("text/event-stream; charset=utf-8")
        client = self.server.events.register()
        try:
            # Prime the client with a full snapshot.
            self._chunk(b"data: " + json.dumps({"type": "state", "state": self.server.state.snapshot()},
                                              ensure_ascii=False).encode() + b"\n\n")
            while True:
                try:
                    event = client.get(timeout=15.0)
                except queue.Empty:
                    self._chunk(b": ping\n\n")
                    continue
                self._chunk(b"data: " + json.dumps(event, ensure_ascii=False).encode() + b"\n\n")
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.server.events.unregister(client)
            self._end_chunked()

    def _stream_video(self) -> None:
        self._start_chunked("application/octet-stream")
        client = self.server.video.register()
        try:
            # A new browser must wait for an IDR. Ask the device to emit one
            # now instead of waiting for the next GOP (up to several seconds).
            if self.server.config.device_host:
                try:
                    ctrl = ControlClient(self.server.config.device_host,
                                         self.server.config.control_port)
                    ctrl.send({"force_idr": 1})
                except Exception:
                    pass
            while True:
                try:
                    ts_ms, data = client.queue.get(timeout=20.0)
                except queue.Empty:
                    # No keyframe yet or the device stopped. Close so the
                    # browser reconnects and re-registers for a keyframe.
                    return
                self._chunk(struct.pack("<IQ", len(data), int(ts_ms)) + data)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.server.video.unregister(client)
            self._end_chunked()

    # ---- POST ------------------------------------------------------------
    def _origin_ok(self) -> bool:
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin", "")
        if origin:
            parsed = urlparse(origin)
            if parsed.netloc != host:
                return False
        hostname = host.rsplit(":", 1)[0].strip("[]")
        if hostname in ("localhost", "127.0.0.1", "::1"):
            return True
        try:
            ipaddress.ip_address(hostname)
            return True
        except ValueError:
            return False

    def do_POST(self) -> None:  # noqa: N802
        path, query = self._url()
        if not self._origin_ok():
            self._send_json({"ok": False, "error": "cross-origin request rejected"}, status=403)
            return
        if not self._token_ok(query):
            self._deny()
            return
        try:
            if path == "/api/params":
                self._post_params()
            elif path == "/api/actions":
                self._post_actions()
            elif path == "/api/mark":
                self._post_mark()
            elif path == "/api/keyframe":
                control = ControlClient(self.server.config.device_host,
                                        self.server.config.control_port)
                ack = control.send({"force_idr": 1})
                self._send_json({"ok": bool(ack.get("ok")), "ack": ack},
                                status=200 if ack.get("ok") else 503)
            elif path == "/api/shutdown":
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                self._send_json({"ok": True})
            else:
                self._send_json({"ok": False, "error": "not found"}, status=404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:  # pragma: no cover - defensive
            traceback.print_exc()
            try:
                self._send_json({"ok": False, "error": "internal error"}, status=500)
            except OSError:
                pass

    def _post_params(self) -> None:
        payload = self._read_json()
        if not isinstance(payload, dict):
            self._send_json({"ok": False, "error": "invalid JSON body"}, status=400)
            return
        values = payload.get("params", payload)
        if not isinstance(values, dict):
            self._send_json({"ok": False, "error": "params must be an object"}, status=400)
            return
        schema = self.server.state.snapshot()["param_schema"]
        errors = validate_params(values, schema)
        if errors:
            self._send_json({"ok": False, "error": "invalid params", "details": errors}, status=400)
            return
        control = ControlClient(self.server.config.device_host,
                                self.server.config.control_port)
        ack = control.send(values)
        if ack.get("ok"):
            self.server.state.set_params(values)
            self.server.recorder.set_params(final=self.server.state.snapshot()["params"])
            self.server.recorder.set_params(change={
                "at": now_iso(), "values": values, "ack": ack.get("detail", ""),
            })
            self.server.recorder.event({"type": "params", "params": values,
                                        "ack": ack.get("detail", ""), "at": now_iso()})
            self.server.events.publish({"type": "params", "params": values, "ack": ack})
        self._send_json({
            "ok": bool(ack.get("ok")),
            "ack": ack,
            "params": self.server.state.snapshot()["params"],
        }, status=200 if ack.get("ok") else 503)

    def _post_actions(self) -> None:
        payload = self._read_json()
        if not isinstance(payload, dict):
            self._send_json({"ok": False, "error": "invalid JSON body"}, status=400)
            return
        action = str(payload.get("action", "")).strip()
        allowed = {"install", "start", "stop", "restart", "status", "verify", "collect", "capture"}
        if action not in allowed:
            self._send_json({"ok": False, "error": f"unsupported action: {action}"}, status=400)
            return
        if not self.server.config.allow_actions:
            self._send_json({"ok": False, "error": "actions disabled; pass --allow-actions"}, status=403)
            return
        if not self.server.action_lock.acquire(blocking=False):
            self._send_json({"ok": False, "error": "another action is already running"}, status=409)
            return
        try:
            result = run_action(self.server.config, action)
        finally:
            self.server.action_lock.release()
        self.server.recorder.event({"type": "action", "action": action,
                                    "result": result, "at": now_iso()})
        self.server.events.publish({"type": "action", "action": action, "result": result})
        self._send_json(result, status=200 if result.get("ok") else 500)

    def _post_mark(self) -> None:
        payload = self._read_json() or {}
        label = str(payload.get("label", "mark"))
        event = {"type": "mark", "label": label, "at": now_iso()}
        self.server.recorder.event(event)
        self.server.events.publish(event)
        self._send_json({"ok": True, "mark": event})


# ---------------------------------------------------------------------------
# Parameter validation and actions
# ---------------------------------------------------------------------------

def validate_params(values: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for name, value in values.items():
        spec = schema.get(name)
        if not isinstance(spec, dict):
            errors.append(f"{name}: unknown parameter")
            continue
        kind = spec.get("type", "number")
        if kind == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                errors.append(f"{name}: expected number")
                continue
        elif kind == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                errors.append(f"{name}: expected integer")
                continue
        elif kind == "boolean":
            if not isinstance(value, bool):
                errors.append(f"{name}: expected boolean")
                continue
        elif kind == "enum":
            if value not in spec.get("enum", []):
                errors.append(f"{name}: must be one of {spec.get('enum', [])}")
                continue
        elif kind == "string":
            if not isinstance(value, str):
                errors.append(f"{name}: expected string")
                continue
        minimum = spec.get("min")
        maximum = spec.get("max")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if isinstance(minimum, (int, float)) and value < minimum:
                errors.append(f"{name}: below minimum {minimum}")
            if isinstance(maximum, (int, float)) and value > maximum:
                errors.append(f"{name}: above maximum {maximum}")
    return errors


def run_action(config: argparse.Namespace, action: str) -> dict[str, Any]:
    if not config.demo_dir:
        return {"ok": False, "error": "--demo-dir is required for actions"}
    if not config.device_host:
        return {"ok": False, "error": "--device-host is required for actions"}
    if shutil.which("ssh") is None:
        return {"ok": False, "error": "ssh not found on PATH; install OpenSSH client"}
    env = dict(os.environ)
    if action in ("start", "restart"):
        env["LIVE_PEER"] = _local_ip_for(config.device_host)
        env["LIVE_ARGS"] = f"--port {config.udp_port} --control-port {config.control_port}"
    timeout = 900 if action in ("install", "capture") else 300
    if action == "capture":
        out = Path(config.demo_dir) / "eval" / "recamera" / "evidence"
        cmd = [sys.executable, str(SCRIPTS / "capture_evidence.py"),
               "--source", "live", "--session", str(config.session_dir),
               "--out", str(out)]
    else:
        cmd = [sys.executable, str(SCRIPTS / "deploy_demo.py"),
               "--demo-dir", str(config.demo_dir),
               "--host", config.device_host,
               "--action", action]
        if action in ("install", "restart"):
            cmd.append("--replace")
        if action in ("start", "restart"):
            cmd.append("--allow-service-stop")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"{action} timed out after {timeout}s"}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": proc.returncode == 0,
        "action": action,
        "returncode": proc.returncode,
        "stdout": redact(proc.stdout[-8000:]),
        "stderr": redact(proc.stderr[-8000:]),
    }


# ---------------------------------------------------------------------------
# UDP ingest
# ---------------------------------------------------------------------------

class UdpIngest(threading.Thread):
    def __init__(self, config: argparse.Namespace, state: LiveState, events: EventHub,
                 video: VideoHub, recorder: SessionRecorder):
        super().__init__(name="udp-ingest", daemon=True)
        self.config = config
        self.state = state
        self.events = events
        self.video = video
        self.recorder = recorder
        self.reassembler = Reassembler(timeout_s=2.0)
        self._stop_event = threading.Event()
        self._sock: socket.socket | None = None
        self._last_publish = 0.0
        self._last_expire = 0.0
        self._last_meta_record = 0.0
        self._caps_warned: set[str] = set()

    def stop(self) -> None:
        self._stop_event.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass

    def run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
        except OSError:
            pass
        sock.settimeout(0.5)
        self._sock = sock
        try:
            sock.bind((self.config.udp_bind, self.config.udp_port))
        except OSError as exc:
            self.state.session_ok = False
            self.state.ingest_error = (f"udp bind {self.config.udp_bind}:"
                                       f"{self.config.udp_port}: {exc}")
            self.state.add_log({"level": "error", "msg": self.state.ingest_error, "at": now_iso()})
            self.events.publish({"type": "error", "error": self.state.ingest_error})
            return
        self.state.ingest_error = ""
        self.state.add_log({"level": "info", "msg": f"UDP listening on {self.config.udp_bind}:{self.config.udp_port}"})
        self.recorder.log(f"[{now_iso()}] host: UDP listening on "
                          f"{self.config.udp_bind}:{self.config.udp_port}")
        while not self._stop_event.is_set():
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                self._housekeeping()
                continue
            except OSError:
                if self._stop_event.is_set():
                    break
                continue
            self.state.note_packet(len(data))
            if not self.state.device_addr:
                self.state.device_addr = f"{addr[0]}:{addr[1]}"
            message = self.reassembler.add(data, addr)
            if message is not None:
                self._dispatch(message.header, message.payload)
            self._housekeeping()

    def _housekeeping(self) -> None:
        now = time.monotonic()
        if now - self._last_expire > 1.0:
            self.reassembler.expire(now)
            self._last_expire = now
            stats = self.reassembler.stats
            self.state.reassembler_stats = {
                "completed": stats.completed,
                "dropped": stats.dropped,
                "duplicates": stats.duplicates,
                "out_of_order": stats.out_of_order,
                "malformed": stats.malformed,
                "packets": stats.packets,
                "bytes": stats.bytes,
            }
        if now - self._last_publish > 1.0:
            self.state.fragment_drops = self.reassembler.stats.dropped
            self.events.publish({"type": "state", "state": self.state.snapshot()})
            self._last_publish = now

    def _warn_cap(self, key: str, message: str) -> None:
        if key in self._caps_warned:
            return
        self._caps_warned.add(key)
        entry = {"level": "warn", "msg": message, "at": now_iso()}
        self.state.add_log(entry)
        self.recorder.log(f"[{entry['at']}] warn: {message}")
        self.events.publish({"type": "log", "log": entry})

    def _cap_shapes(self, meta: dict[str, Any]) -> dict[str, Any]:
        """Defend the SSE fan-out and browser from an oversized meta."""
        changed = False
        result = dict(meta)
        for key in ("shapes", "annotations"):
            value = result.get(key)
            if not isinstance(value, list):
                continue
            shapes: list[Any] = value
            if len(shapes) > MAX_SHAPES:
                self._warn_cap(f"{key}.count",
                               f"truncated {key}: {len(shapes)} -> {MAX_SHAPES}")
                shapes = shapes[:MAX_SHAPES]
                changed = True
            capped: list[Any] = []
            for shape in shapes:
                if not isinstance(shape, dict):
                    continue
                pts_key = "pts" if isinstance(shape.get("pts"), list) else (
                    "points" if isinstance(shape.get("points"), list) else None)
                if pts_key and len(shape[pts_key]) > MAX_SHAPE_POINTS:
                    self._warn_cap(f"{key}.points",
                                   f"truncated {key} points: "
                                   f"{len(shape[pts_key])} -> {MAX_SHAPE_POINTS}")
                    shape = dict(shape)
                    shape[pts_key] = shape[pts_key][:MAX_SHAPE_POINTS]
                    changed = True
                capped.append(shape)
            if changed:
                result[key] = capped
        return result if changed else meta

    def _dispatch(self, header: dict[str, Any], payload: bytes) -> None:
        self.state.touch()
        msg_type = header["msg_type"]
        if msg_type == VIDEO:
            header = dict(header)
            header["_payload_size"] = len(payload)
            self.state.note_video(header, len(payload))
            self.recorder.note_video(header)
            self.video.broadcast(int(header["ts_ms"]), payload, bool(header["keyframe"]))
            self.recorder.video(payload)
        elif msg_type == META:
            if len(payload) > META_MAX_BYTES:
                self._warn_cap("meta.size",
                               f"meta dropped: {len(payload)} B > {META_MAX_BYTES} B")
                return
            try:
                meta = decode_json(payload)
            except (ValueError, UnicodeDecodeError):
                self.reassembler.stats.malformed += 1
                self._warn_cap("meta.decode", "invalid META JSON from device")
                return
            if isinstance(meta, dict):
                meta = self._cap_shapes(meta)
                self.state.update_meta(meta)
                self.recorder.note_meta()
                now = time.monotonic()
                if now - self._last_meta_record >= 1.0:
                    self._last_meta_record = now
                    self.recorder.event({"type": "meta", "meta": meta, "at": now_iso()})
                self.events.publish({"type": "meta", "meta": meta})
        elif msg_type == HELLO:
            try:
                hello = decode_json(payload)
            except (ValueError, UnicodeDecodeError):
                self.reassembler.stats.malformed += 1
                self._warn_cap("hello.decode", "invalid HELLO JSON from device")
                return
            if isinstance(hello, dict):
                self.state.update_hello(hello)
                self.recorder.set_device({
                    "host": self.config.device_host or "",
                    "addr": self.state.device_addr,
                    "hello": hello,
                })
                self.recorder.event({"type": "hello", "hello": hello, "at": now_iso()})
                self.events.publish({"type": "hello", "hello": hello})
        elif msg_type == LOG:
            text = payload.decode("utf-8", "replace").rstrip("\n")
            entry = {"level": "info", "msg": text, "at": now_iso()}
            try:
                obj = decode_json(payload)
                if isinstance(obj, dict):
                    entry.update({"level": str(obj.get("level", "info")),
                                  "msg": str(obj.get("msg", text))})
                    if "at" in obj:
                        entry["at"] = str(obj["at"])
            except (ValueError, UnicodeDecodeError):
                pass
            self.state.add_log(entry)
            self.recorder.log(f"[{entry['at']}] {entry['level']}: {entry['msg']}")
            self.recorder.event({"type": "log", "log": entry})
            self.events.publish({"type": "log", "log": entry})
        elif msg_type == SNAPSHOT:
            seq = int(header["seq"])
            self.recorder.snapshot(payload, seq)
            self.recorder.event({"type": "snapshot", "seq": seq, "size": len(payload), "at": now_iso()})
            self.events.publish({"type": "snapshot", "seq": seq, "size": len(payload)})
        elif msg_type == STATS:
            try:
                stats = decode_json(payload)
            except (ValueError, UnicodeDecodeError):
                self.reassembler.stats.malformed += 1
                self._warn_cap("stats.decode", "invalid STATS JSON from device")
                return
            if isinstance(stats, dict):
                self.state.update_stats(stats)
                self.events.publish({"type": "stats", "stats": stats})


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

def load_param_schema(demo_dir: Path | None) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Return (params_schema, default_values, implemented) from demo.json."""
    if not demo_dir:
        return {}, {}, False
    manifest_path = Path(demo_dir) / "demo.json"
    if not manifest_path.is_file():
        return {}, {}, False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, {}, False
    live = manifest.get("live")
    if not isinstance(live, dict):
        return {}, {}, False
    implemented = bool(live.get("implemented", False))
    schema = live.get("params")
    if not isinstance(schema, dict):
        return {}, {}, implemented
    defaults = {
        name: spec.get("default")
        for name, spec in schema.items()
        if isinstance(spec, dict) and "default" in spec
    }
    return schema, defaults, implemented


def load_live_perf(demo_dir: Path | None) -> dict[str, Any]:
    """Return the demo.json ``live.perf`` thresholds (empty when absent)."""
    if not demo_dir:
        return {}
    manifest_path = Path(demo_dir) / "demo.json"
    if not manifest_path.is_file():
        return {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    live = manifest.get("live")
    if not isinstance(live, dict):
        return {}
    perf = live.get("perf")
    return dict(perf) if isinstance(perf, dict) else {}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--demo-dir", type=Path, help="demo workspace; enables actions and demo.json params")
    parser.add_argument("--device-host", help="reCamera IP for parameter control and actions")
    parser.add_argument("--bind", default="127.0.0.1", help="HTTP bind address (default 127.0.0.1)")
    parser.add_argument("--http-port", type=int, default=8090)
    parser.add_argument("--udp-bind", default="0.0.0.0", help="UDP bind address (default 0.0.0.0)")
    parser.add_argument("--udp-port", type=int, default=DEFAULT_VIDEO_PORT)
    parser.add_argument("--control-port", type=int, default=DEFAULT_CONTROL_PORT)
    parser.add_argument("--token", default="", help="require this token for API access")
    parser.add_argument("--allow-actions", action="store_true",
                        help="allow the UI to run deploy_demo.py (needs ssh in PATH)")
    parser.add_argument("--session-dir", type=Path, help="override the session output directory")
    parser.add_argument("--record-video", action="store_true", help="also save the raw H.264 stream")
    parser.add_argument("--ui-dir", type=Path, default=UI_ROOT, help="static UI directory")
    parser.add_argument("--open", action="store_true", help="open the UI in the default browser")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not Path(args.ui_dir).is_dir():
        print(f"ERROR: UI directory not found: {args.ui_dir}", file=sys.stderr)
        return 2
    if args.allow_actions and not args.token:
        args.token = secrets.token_urlsafe(18)
        print(f"WARNING: --allow-actions requires a token; generated token: {args.token}",
              file=sys.stderr)
    if args.bind not in ("127.0.0.1", "localhost", "::1") and not args.token:
        args.token = secrets.token_urlsafe(18)
        print(f"WARNING: binding {args.bind} without --token; generated token: {args.token}",
              file=sys.stderr)
    demo_dir = Path(args.demo_dir).resolve() if args.demo_dir else None
    demo_name = demo_dir.name if demo_dir else "live"
    schema, defaults, implemented = load_param_schema(demo_dir)
    if not schema:
        schema = {
            "confidence": {"type": "number", "min": 0.05, "max": 0.95, "step": 0.05, "default": 0.60},
        }
        defaults = {"confidence": defaults.get("confidence", 0.60)}
    session_dir = args.session_dir or (demo_dir / "eval" / "live" / _utc_stamp()
                                       if demo_dir else Path.cwd() / "live-session" / _utc_stamp())
    args.session_dir = session_dir

    state = LiveState(demo_name=demo_name, params=dict(defaults), param_schema=schema,
                      session_dir=session_dir.as_posix(), implemented=implemented,
                      perf=load_live_perf(demo_dir))
    events = EventHub()
    video = VideoHub()
    recorder = SessionRecorder(session_dir, demo_name=demo_name, record_video=args.record_video)
    recorder.set_params(initial=dict(defaults))

    ingest = UdpIngest(args, state, events, video, recorder)
    ingest.start()

    server = LiveHTTPServer((args.bind, args.http_port), Handler,
                            state=state, events=events, video=video,
                            recorder=recorder, config=args)
    host = "127.0.0.1" if args.bind in ("0.0.0.0", "::") else args.bind
    query = f"?token={quote(args.token, safe='')}" if args.token else ""
    url = f"http://{host}:{args.http_port}/{query}"
    if not args.quiet:
        print(f"live UI: {url}")
        print(f"UDP:     {args.udp_bind}:{args.udp_port}  control: "
              f"{args.device_host or '-'}:{args.control_port}")
        print(f"session: {session_dir}")
        if args.device_host:
            print(f"device:  {args.device_host}")
        peer = _local_ip_for(args.device_host)
        if peer not in ("127.0.0.1", "0.0.0.0"):
            print(f"device peer hint: --peer {peer}")
            if args.bind not in ("127.0.0.1", "localhost", "::1"):
                lan = f"http://{peer}:{args.http_port}/{query}"
                print(f"LAN UI:  {lan}")
    if args.open:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    ok = True
    # SIGTERM is how launch_demo.py and any process supervisor stop this
    # server. The default action kills the interpreter before the finally block
    # runs, so session.json (the artifact capture_evidence.py consumes) would
    # never be written. A flag + shutdown() is used instead of raising: raising
    # from a signal handler can fire inside threading internals and leave the
    # recorder half-written.
    stop_requested = threading.Event()

    def _on_term(signum, frame):  # noqa: ARG001
        stop_requested.set()
        # shutdown() must not run on the serve_forever thread.
        threading.Thread(target=server.shutdown, daemon=True).start()

    try:
        signal.signal(signal.SIGTERM, _on_term)
    except (ValueError, OSError):
        pass

    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        ok = state.session_ok and state.video_frames > 0
        ingest.stop()
        ingest.join(timeout=3)
        server.server_close()
        for event in ({"type": "state", "state": state.snapshot()},
                      {"type": "closed", "ok": ok}):
            events.publish(event)
        path = recorder.close(ok=ok)
        if not args.quiet:
            print(f"session written: {path}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())