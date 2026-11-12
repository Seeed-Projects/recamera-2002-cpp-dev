from __future__ import annotations

import argparse
import http.client
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

import serve_demo
from devkit import live


def free_port(kind: int) -> int:
    sock = socket.socket(socket.AF_INET, kind)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def wait_health(port: int, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.5):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("live service did not start")


def http_json(port: int, path: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def http_post(port: int, path: str, payload: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


@pytest.fixture
def service(tmp_path: Path):
    http_port = free_port(socket.SOCK_STREAM)
    udp_port = free_port(socket.SOCK_DGRAM)
    control_port = free_port(socket.SOCK_DGRAM)
    session = tmp_path / "session"
    config = argparse.Namespace(
        device_host="127.0.0.1",
        control_port=control_port,
        ui_dir=serve_demo.UI_ROOT,
        token="",
        allow_actions=False,
        demo_dir=tmp_path,
        session_dir=session,
        udp_bind="127.0.0.1",
        udp_port=udp_port,
    )
    state = serve_demo.LiveState(
        demo_name="live_test",
        params={"confidence": 0.6},
        param_schema={"confidence": {"type": "number", "min": 0.05, "max": 0.95, "default": 0.6}},
        session_dir=str(session),
    )
    events = serve_demo.EventHub()
    video = serve_demo.VideoHub()
    recorder = serve_demo.SessionRecorder(session, demo_name="live_test")
    ingest = serve_demo.UdpIngest(config, state, events, video, recorder)
    ingest.start()
    server = serve_demo.LiveHTTPServer(
        ("127.0.0.1", http_port), serve_demo.Handler,
        state=state, events=events, video=video, recorder=recorder, config=config,
    )
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    wait_health(http_port)
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    ns = SimpleNamespace(
        http_port=http_port, udp_port=udp_port, control_port=control_port,
        state=state, server=server, ingest=ingest, recorder=recorder,
        session=session, sender=sender,
    )

    def send(packets):
        for packet in packets:
            sender.sendto(packet, ("127.0.0.1", udp_port))

    ns.send = send
    yield ns

    sender.close()
    ingest.stop()
    ingest.join(timeout=3)
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
    recorder.close(ok=True)


def send_standard_batch(service, include_video: bool = False) -> bytes:
    now = int(time.time() * 1000)
    hello = {
        "v": 1, "device": "reCamera 2002", "demo": "live_test",
        "video": {"codec": "h264", "width": 1920, "height": 1080, "fps": 30},
        "model": {"width": 640, "height": 640, "preprocess": "stretch"},
        "params": {"confidence": 0.6}, "labels": ["person"],
    }
    service.send(live.fragment(live.HELLO, live.encode_json(hello), seq=1, ts_ms=now))
    meta = {
        "seq": 2, "video_seq": 1, "ts_ms": now, "infer_ms": 12.0, "fps": 25.0,
        "frame_w": 1920, "frame_h": 1080, "model_w": 640, "model_h": 640,
        "preprocess": "stretch", "dets": [
            {"x": 0.5, "y": 0.5, "w": 0.1, "h": 0.2, "score": 0.83, "target": 0},
        ],
        "annotations": [
            {"type": "polyline", "space": "frame",
             "points": [[0.1, 0.9], [0.5, 0.5]], "color": "#3ddc97", "width": 4},
        ],
    }
    service.send(live.fragment(live.META, live.encode_json(meta), seq=2, ts_ms=now))
    service.send(live.fragment(live.LOG, live.encode_json({"level": "info", "msg": "ready"}), seq=3, ts_ms=now))
    service.send(live.fragment(live.SNAPSHOT, b"\xff\xd8fake\xff\xd9", seq=4, ts_ms=now,
                               width=1920, height=1080))
    service.send(live.fragment(live.STATS, live.encode_json({"fps": 30.0, "bitrate_kbps": 8000}), seq=5, ts_ms=now))
    access_unit = bytes.fromhex("0000000165888421a0")
    if include_video:
        service.send(live.fragment(live.VIDEO, access_unit, seq=6, ts_ms=now,
                                   width=1920, height=1080, flags=live.FLAG_KEYFRAME))
    return access_unit


def test_state_and_metadata(service):
    send_standard_batch(service)
    deadline = time.time() + 3
    while time.time() < deadline:
        status, state = http_json(service.http_port, "/api/state")
        if state.get("meta_count") and state.get("hello") and state.get("stats", {}).get("bitrate_kbps"):
            break
        time.sleep(0.05)
    assert status == 200
    assert state["hello"]["demo"] == "live_test"
    assert state["meta_count"] == 1
    assert state["meta"]["annotations"][0]["type"] == "polyline"
    assert state["params"]["confidence"] == 0.6
    assert state["device_addr"].startswith("127.0.0.1:")
    assert state["stats"]["bitrate_kbps"] == 8000


def test_video_stream_starts_at_keyframe(service):
    access_unit = send_standard_batch(service)
    connection = http.client.HTTPConnection("127.0.0.1", service.http_port, timeout=5)
    connection.request("GET", "/api/video")
    response = connection.getresponse()
    assert response.status == 200
    time.sleep(0.2)
    non_key = bytes.fromhex("00000001419a")
    service.send(live.fragment(live.VIDEO, non_key, seq=8, ts_ms=int(time.time() * 1000),
                               width=1920, height=1080, flags=0))
    time.sleep(0.1)
    service.send(live.fragment(live.VIDEO, access_unit, seq=9, ts_ms=int(time.time() * 1000),
                               width=1920, height=1080, flags=live.FLAG_KEYFRAME))
    body = response.read(12 + len(access_unit))
    connection.close()
    assert int.from_bytes(body[:4], "little") == len(access_unit)
    assert body[12:12 + len(access_unit)] == access_unit


def test_param_validation_and_action_gate(service):
    status, body = http_post(service.http_port, "/api/params", {"params": {"confidence": 1.5}})
    assert status == 400
    assert body["ok"] is False
    status, body = http_post(service.http_port, "/api/actions", {"action": "start"})
    assert status == 403
    assert "allow-actions" in body["error"]


def test_param_control_ack(service):
    ack_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    ack_socket.bind(("127.0.0.1", service.control_port))
    ack_socket.settimeout(5)
    result: dict = {}

    def post() -> None:
        result["value"] = http_post(service.http_port, "/api/params", {"params": {"confidence": 0.7}})

    worker = threading.Thread(target=post)
    worker.start()
    data, address = ack_socket.recvfrom(4096)
    command = live.parse_control(data)
    assert command["command"] == "SET"
    assert command["params"]["confidence"] == 0.7
    seq = command["params"]["seq"]
    ack_socket.sendto(f"RCC1 ACK seq={seq} ok=1 applied=confidence\n".encode(), address)
    worker.join(timeout=5)
    ack_socket.close()
    assert result["value"][0] == 200
    assert result["value"][1]["ok"] is True
    assert service.state.params["confidence"] == 0.7


def test_load_param_schema_reads_implemented(tmp_path: Path):
    demo = tmp_path / "demo"
    demo.mkdir()
    (demo / "demo.json").write_text(json.dumps({
        "schema_version": 1, "name": "x", "version": "1",
        "device": {"model": "reCamera 2002"},
        "build": {"kind": "cpp", "binary": "x"},
        "runtime": {"executable": "x"},
        "evaluation": {"task": "custom"},
        "live": {
            "transport": "udp",
            "implemented": True,
            "video": {"codec": "h264", "width": 1920, "height": 1080, "fps": 30},
            "params": {"confidence": {"type": "number", "default": 0.6}},
        },
    }), encoding="utf-8")
    schema, defaults, implemented = serve_demo.load_param_schema(demo)
    assert implemented is True
    assert defaults["confidence"] == 0.6
    assert schema["confidence"]["type"] == "number"


def test_session_and_live_evidence(service, tmp_path: Path):
    send_standard_batch(service)
    deadline = time.time() + 3
    frames = []
    while time.time() < deadline:
        frames = list((service.session / "frames").glob("*.jpg"))
        if frames:
            break
        time.sleep(0.05)
    assert frames, "snapshot was not written"
    assert (service.session / "events.jsonl").is_file()
    service.recorder.close(ok=True)
    assert (service.session / "session.json").is_file()

    out = tmp_path / "evidence"
    import capture_evidence
    code = capture_evidence.main([
        "--source", "live", "--session", str(service.session), "--out", str(out),
    ])
    assert code == 0
    report = json.loads((out / "evidence.json").read_text(encoding="utf-8"))
    assert report["source"] == "live"
    assert report["ok"] is True
    assert any(item["path"].startswith("live-") for item in report["artifacts"])

def test_meta_shape_caps(service):
    now = int(time.time() * 1000)
    shapes = [
        {"kind": "polyline", "space": "frame",
         "pts": [[0.1, 0.9], [0.5, 0.5]]}
        for _ in range(20)
    ]
    meta = {
        "seq": 1, "ts_ms": now, "frame_w": 1920, "frame_h": 1080,
        "model_w": 640, "model_h": 640, "preprocess": "stretch",
        "shapes": shapes,
    }
    service.send(live.fragment(live.META, live.encode_json(meta), seq=1, ts_ms=now))
    deadline = time.time() + 3
    while time.time() < deadline:
        state = service.state.snapshot()
        if state.get("meta_count"):
            break
        time.sleep(0.05)
    assert len(state["meta"]["shapes"]) == serve_demo.MAX_SHAPES
    assert any("truncated shapes" in entry.get("msg", "") for entry in state["logs"])


def test_meta_size_cap_is_rejected(service):
    now = int(time.time() * 1000)
    meta = {
        "seq": 1, "ts_ms": now, "frame_w": 1920, "frame_h": 1080,
        "padding": "x" * (serve_demo.META_MAX_BYTES + 100),
    }
    service.send(live.fragment(live.META, live.encode_json(meta), seq=1, ts_ms=now))
    time.sleep(0.4)
    state = service.state.snapshot()
    assert state["meta_count"] == 0
    assert any("meta dropped" in entry.get("msg", "") for entry in state["logs"])


def test_udp_bind_error_is_reported(tmp_path: Path):
    busy = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    busy.bind(("127.0.0.1", 0))
    port = busy.getsockname()[1]
    config = argparse.Namespace(udp_bind="127.0.0.1", udp_port=port,
                                device_host=None, control_port=0)
    state = serve_demo.LiveState()
    events = serve_demo.EventHub()
    video = serve_demo.VideoHub()
    recorder = serve_demo.SessionRecorder(tmp_path / "session", demo_name="x")
    ingest = serve_demo.UdpIngest(config, state, events, video, recorder)
    ingest.start()
    ingest.join(timeout=2)
    try:
        assert "udp bind" in state.ingest_error
        assert state.snapshot()["ingest_error"] == state.ingest_error
        assert state.snapshot()["session_ok"] is False
    finally:
        recorder.close(ok=False)
        busy.close()


def test_ui_caps_match_service(skill_root):
    text = (skill_root / "templates" / "live-ui" / "app.js").read_text(encoding="utf-8")
    assert f"const MAX_SHAPES = {serve_demo.MAX_SHAPES};" in text
    assert f"const MAX_SHAPE_POINTS = {serve_demo.MAX_SHAPE_POINTS};" in text


def test_ui_schema_compare_ignores_key_order(skill_root):
    """hello's params_schema and the demo.json-merged param_schema only differ
    in object key order; comparing raw JSON strings made the browser rebuild
    the whole model-parameters panel twice per hello (visible flash)."""
    text = (skill_root / "templates" / "live-ui" / "app.js").read_text(encoding="utf-8")
    assert "function canonicalJson(value)" in text
    assert "const key = canonicalJson(schema);" in text
    assert "const key = JSON.stringify(schema);" not in text
    # The raw hello copy must not override the service-merged schema.
    assert "if (!schemaKey && hello && hello.params_schema)" in text


def test_cpp_caps_match_service(skill_root):
    header = (skill_root / "templates" / "live" / "src" / "live_udp.h").read_text(encoding="utf-8")
    source = (skill_root / "templates" / "live" / "src" / "live_udp.cpp").read_text(encoding="utf-8")
    assert f"kMaxShapes = {serve_demo.MAX_SHAPES}" in header
    assert f"kMaxShapePoints = {serve_demo.MAX_SHAPE_POINTS}" in header
    assert f"kMetaJsonBudget = {serve_demo.META_MAX_BYTES}" in source


def test_post_requires_json_and_same_origin(service):
    def post(headers):
        request = urllib.request.Request(
            f"http://127.0.0.1:{service.http_port}/api/params",
            data=b'{"params":{"confidence":0.7}}',
            headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status
        except urllib.error.HTTPError as exc:
            return exc.code

    assert post({"Content-Type": "text/plain"}) == 400
    assert post({"Content-Type": "application/json",
                 "Origin": "http://evil.example"}) == 403
    assert post({"Content-Type": "application/json",
                 "Origin": f"http://127.0.0.1:{service.http_port}"}) in (400, 503)


def test_token_required(tmp_path):
    http_port = free_port(socket.SOCK_STREAM)
    session = tmp_path / "session"
    config = argparse.Namespace(
        device_host=None, control_port=free_port(socket.SOCK_DGRAM),
        ui_dir=serve_demo.UI_ROOT, token="secret", allow_actions=False,
        demo_dir=None, session_dir=session, udp_bind="127.0.0.1",
        udp_port=free_port(socket.SOCK_DGRAM),
    )
    state = serve_demo.LiveState(session_dir=str(session))
    events = serve_demo.EventHub()
    video = serve_demo.VideoHub()
    recorder = serve_demo.SessionRecorder(session, demo_name="token_test")
    server = serve_demo.LiveHTTPServer(
        ("127.0.0.1", http_port), serve_demo.Handler,
        state=state, events=events, video=video, recorder=recorder, config=config,
    )
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    wait_health(http_port)
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{http_port}/api/state")
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(request, timeout=5)
        assert excinfo.value.code == 401
        status, body = http_json(http_port, "/api/state?token=secret")
        assert status == 200 and body["ok"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        recorder.close(ok=False)


def test_meta_point_cap(service):
    now = int(time.time() * 1000)
    meta = {
        "seq": 1, "ts_ms": now, "frame_w": 1920, "frame_h": 1080,
        "shapes": [{"kind": "polyline", "space": "frame",
                    "pts": [[0.1, 0.9]] * 100}],
    }
    service.send(live.fragment(live.META, live.encode_json(meta), seq=1, ts_ms=now))
    deadline = time.time() + 3
    while time.time() < deadline:
        state = service.state.snapshot()
        if state.get("meta_count"):
            break
        time.sleep(0.05)
    assert len(state["meta"]["shapes"][0]["pts"]) == serve_demo.MAX_SHAPE_POINTS


def test_sse_initial_state(service):
    connection = http.client.HTTPConnection("127.0.0.1", service.http_port, timeout=5)
    connection.request("GET", "/api/events")
    response = connection.getresponse()
    assert response.status == 200
    line = response.readline()
    connection.close()
    assert line.startswith(b"data: ")
    event = json.loads(line[len(b"data: "):])
    assert event["type"] == "state"
    assert "state" in event


def test_malformed_meta_is_surfaced(service):
    now = int(time.time() * 1000)
    service.send(live.fragment(live.META, b"{not-json", seq=77, ts_ms=now))
    deadline = time.time() + 3
    state = None
    while time.time() < deadline:
        state = service.state.snapshot()
        if (state.get("reassembler") or {}).get("malformed", 0) > 0:
            break
        time.sleep(0.1)
    assert state is not None
    assert state["reassembler"]["malformed"] >= 1
    assert any("invalid META JSON" in entry.get("msg", "") for entry in state["logs"])


def test_load_live_perf(tmp_path: Path):
    demo = tmp_path / "demo"
    demo.mkdir()
    (demo / "demo.json").write_text(json.dumps({
        "live": {"perf": {"max_result_age_ms": 350, "min_detect_fps": 2}},
    }), encoding="utf-8")
    assert serve_demo.load_live_perf(demo) == {
        "max_result_age_ms": 350, "min_detect_fps": 2,
    }
    assert serve_demo.load_live_perf(tmp_path) == {}


def test_pipeline_gate_uses_device_age():
    state = serve_demo.LiveState(
        perf={"max_result_age_ms": 300, "min_detect_fps": 1, "min_video_fps": 10},
    )
    state.stats = {"fps": 30}

    # No samples yet: the gate must say unmeasured, not pass.
    fresh = state.snapshot()["pipeline"]
    assert fresh["samples"] == 0
    assert fresh["ok"] is None
    assert fresh["measured"] is False

    state.meta_age_samples = [(i * 0.1, 120.0) for i in range(12)]
    state.meta_times = [i * 0.1 for i in range(12)]
    ok = state.snapshot()["pipeline"]
    assert ok["ok"] is True
    assert ok["result_age_ms"] == 120.0
    assert ok["detect_fps"] > 5

    # Same update rate, but the queue bug: results 3 s old.
    state.meta_age_samples = [(i * 0.1, 3000.0) for i in range(12)]
    bad = state.snapshot()["pipeline"]
    assert bad["ok"] is False
    assert any(c["name"] == "result_age_ms" and not c["ok"] for c in bad["checks"])
