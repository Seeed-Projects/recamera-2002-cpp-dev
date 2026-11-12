from __future__ import annotations

import argparse
import json
import socket
from pathlib import Path

import pytest

import launch_demo


def free_port(kind: int = socket.SOCK_STREAM) -> int:
    sock = socket.socket(socket.AF_INET, kind)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def make_demo(tmp_path: Path) -> Path:
    demo = tmp_path / "live_demo"
    demo.mkdir()
    (demo / "demo.json").write_text(json.dumps({
        "schema_version": 1,
        "name": "live_demo",
        "version": "1.0.0",
        "device": {"model": "reCamera 2002"},
        "build": {"kind": "cpp", "binary": "live_demo"},
        "runtime": {"executable": "live_demo"},
        "evaluation": {"task": "custom"},
        "live": {
            "transport": "udp",
            "implemented": False,
            "video": {"codec": "h264", "width": 1920, "height": 1080, "fps": 30},
            "params": {
                "confidence": {"type": "number", "min": 0.05, "max": 0.95, "default": 0.6},
            },
        },
    }), encoding="utf-8")
    return demo


def test_url_and_free_port():
    assert launch_demo._url("192.168.2.101", 8090, "tok") == \
        "http://192.168.2.101:8090/?token=tok"
    assert launch_demo._url("127.0.0.1", 8090, "") == "http://127.0.0.1:8090/"
    assert launch_demo._url("127.0.0.1", 8090, "a&b") == \
        "http://127.0.0.1:8090/?token=a%26b"
    port = launch_demo._free_tcp_port("127.0.0.1", 0)
    assert 0 < port < 65536
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))


def test_show_cmd_masks_token():
    shown = launch_demo._show_cmd([
        "python3", "serve_demo.py", "--token", "super-secret", "--bind", "0.0.0.0",
    ])
    assert "super-secret" not in shown
    assert "--token ***" in shown


def test_launcher_start_status_stop(tmp_path, capsys):
    demo = make_demo(tmp_path)
    http_port = free_port()
    udp_port = free_port(socket.SOCK_DGRAM)
    control_port = free_port(socket.SOCK_DGRAM)
    args = [
        "--demo-dir", str(demo),
        "--bind", "127.0.0.1",
        "--http-port", str(http_port),
        "--udp-port", str(udp_port),
        "--control-port", str(control_port),
        "--token", "tok",
        "--no-deploy", "--no-build", "--no-smoke", "--no-validate",
        "--no-actions", "--wait", "1", "--json",
    ]
    try:
        assert launch_demo.main(args) == 0
        output = capsys.readouterr().out
        result = json.loads(output.strip().splitlines()[-1])
        assert result["url"] == f"http://127.0.0.1:{http_port}/?token=tok"
        assert result["status"] == "placeholder"
        assert result["implemented"] is False
        assert result["session_dir"]
        assert launch_demo._health(http_port, "127.0.0.1")

        assert launch_demo.main(["--demo-dir", str(demo), "--status"]) == 0
        status = capsys.readouterr().out
        assert "LIVE_STATUS=running" in status
        assert "LIVE_URL=" in status
    finally:
        launch_demo.main(["--demo-dir", str(demo), "--stop"])
    assert not launch_demo._health(http_port, "127.0.0.1")
    assert launch_demo.main(["--demo-dir", str(demo), "--status"]) == 1
    assert "LIVE_STATUS=stopped" in capsys.readouterr().out


def test_launcher_reuses_running_server(tmp_path, capsys):
    demo = make_demo(tmp_path)
    http_port = free_port()
    udp_port = free_port(socket.SOCK_DGRAM)
    control_port = free_port(socket.SOCK_DGRAM)
    base = [
        "--demo-dir", str(demo), "--bind", "127.0.0.1",
        "--http-port", str(http_port), "--udp-port", str(udp_port),
        "--control-port", str(control_port), "--token", "tok",
        "--no-deploy", "--no-build", "--no-smoke", "--no-validate",
        "--no-actions", "--wait", "1",
    ]
    try:
        assert launch_demo.main(base) == 0
        capsys.readouterr()
        assert launch_demo.main(base) == 0
        out = capsys.readouterr().out
        assert "LIVE_STATUS=reused" in out
        assert f"127.0.0.1:{http_port}" in out
    finally:
        launch_demo.main(["--demo-dir", str(demo), "--stop"])


def test_missing_live_section_is_rejected(tmp_path):
    demo = tmp_path / "plain"
    demo.mkdir()
    (demo / "demo.json").write_text(json.dumps({
        "schema_version": 1, "name": "plain", "version": "1.0.0",
        "device": {"model": "reCamera 2002"},
        "build": {"kind": "cpp", "binary": "plain"},
        "runtime": {"executable": "plain"},
        "evaluation": {"task": "custom"},
    }), encoding="utf-8")
    assert launch_demo.main(["--demo-dir", str(demo), "--no-deploy"]) == 2

def test_resolve_live_ports_from_manifest():
    manifest = {"live": {"udp": {"video_port": 5100, "control_port": 5101}}}
    args = argparse.Namespace(udp_port=None, control_port=None)
    assert launch_demo._resolve_live_ports(manifest, args) == (5100, 5101)
    args.udp_port = 5200
    assert launch_demo._resolve_live_ports(manifest, args) == (5200, 5101)
    empty = {"live": {"transport": "udp"}}
    args = argparse.Namespace(udp_port=None, control_port=None)
    assert launch_demo._resolve_live_ports(empty, args) == (
        launch_demo.DEFAULT_VIDEO_PORT, launch_demo.DEFAULT_CONTROL_PORT)


def test_manifest_port_change_restarts_service(tmp_path, capsys):
    demo = make_demo(tmp_path)
    manifest_path = demo / "demo.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    http_port = free_port()
    first_udp = free_port(socket.SOCK_DGRAM)
    first_control = free_port(socket.SOCK_DGRAM)
    manifest["live"]["udp"] = {"video_port": first_udp, "control_port": first_control}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    base = [
        "--demo-dir", str(demo), "--bind", "127.0.0.1",
        "--http-port", str(http_port), "--token", "tok",
        "--no-deploy", "--no-build", "--no-smoke", "--no-validate",
        "--no-actions", "--wait", "1",
    ]
    try:
        assert launch_demo.main(base) == 0
        capsys.readouterr()
        second_udp = free_port(socket.SOCK_DGRAM)
        second_control = free_port(socket.SOCK_DGRAM)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["live"]["udp"] = {"video_port": second_udp, "control_port": second_control}
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        assert launch_demo.main(base) == 0
        out = capsys.readouterr().out
        assert "LIVE_STATUS=reused" not in out
        state = json.loads((demo / "dist" / "live" / "server.json").read_text(encoding="utf-8"))
        assert state["udp_port"] == second_udp
        assert state["control_port"] == second_control
    finally:
        launch_demo.main(["--demo-dir", str(demo), "--stop"])


def test_occupied_udp_port_falls_back(tmp_path, capsys):
    demo = make_demo(tmp_path)
    manifest_path = demo / "demo.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    busy = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    busy.bind(("0.0.0.0", 0))
    busy_port = busy.getsockname()[1]
    manifest["live"]["udp"] = {"video_port": busy_port, "control_port": busy_port + 1}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    http_port = free_port()
    args = [
        "--demo-dir", str(demo), "--bind", "127.0.0.1",
        "--http-port", str(http_port), "--token", "tok",
        "--no-deploy", "--no-build", "--no-smoke", "--no-validate",
        "--no-actions", "--wait", "1",
    ]
    try:
        assert launch_demo.main(args) == 0
        out = capsys.readouterr().out
        assert "UDP port" in out and "busy" in out
        state = json.loads((demo / "dist" / "live" / "server.json").read_text(encoding="utf-8"))
        assert state["udp_port"] != busy_port
        assert state["control_port"] == busy_port + 1
    finally:
        busy.close()
        launch_demo.main(["--demo-dir", str(demo), "--stop"])


def test_allow_actions_alias():
    args = launch_demo.parse_args(["--demo-dir", "x", "--allow-actions"])
    assert args.actions is True
    args = launch_demo.parse_args(["--demo-dir", "x", "--no-actions"])
    assert args.actions is False


def test_launch_state_surfaces_ingest_error(tmp_path):
    args = argparse.Namespace(host="192.168.42.1", udp_port=5000, control_port=5001)
    result = launch_demo._launch_state(
        tmp_path, args,
        url="http://127.0.0.1:8090/?token=t",
        local_url="http://127.0.0.1:8090/?token=t",
        token="t", port=8090, pid=123, session_dir=tmp_path / "s",
        peer="192.168.2.101",
        video_state={"implemented": True, "ingest_error": "udp bind 0.0.0.0:5000: busy",
                     "video": {"frames": 0}},
        deploy_ok=True, warnings=[],
    )
    assert result["status"] == "degraded"
    assert "udp bind" in result["ingest_error"]
    assert any("udp bind" in warning for warning in result["warnings"])


def test_specific_bind_is_probed_on_that_address(tmp_path, capsys):
    demo = make_demo(tmp_path)
    http_port = free_port()
    udp_port = free_port(socket.SOCK_DGRAM)
    control_port = free_port(socket.SOCK_DGRAM)
    args = [
        "--demo-dir", str(demo), "--bind", "127.0.0.2",
        "--http-port", str(http_port), "--udp-port", str(udp_port),
        "--control-port", str(control_port), "--token", "tok",
        "--no-deploy", "--no-build", "--no-smoke", "--no-validate",
        "--no-actions", "--wait", "1",
    ]
    try:
        assert launch_demo.main(args) == 0
        assert launch_demo._health(http_port, "127.0.0.2")
    finally:
        launch_demo.main(["--demo-dir", str(demo), "--stop"])


def test_launch_state_degrades_when_perf_gate_fails(tmp_path):
    args = argparse.Namespace(host="192.168.42.1", udp_port=5000, control_port=5001)
    pipeline = {
        "enforced": True, "ok": False, "result_age_ms": 3000.0, "detect_fps": 5.0,
        "checks": [{"name": "result_age_ms", "value": 3000.0, "limit": 500, "ok": False}],
    }
    result = launch_demo._launch_state(
        tmp_path, args,
        url="http://127.0.0.1:8090/?token=t",
        local_url="http://127.0.0.1:8090/?token=t",
        token="t", port=8090, pid=123, session_dir=tmp_path / "s",
        peer="192.168.2.101",
        video_state={"implemented": True, "video": {"frames": 10}, "pipeline": pipeline},
        deploy_ok=True, warnings=[],
    )
    assert result["status"] == "degraded"
    assert result["pipeline"]["result_age_ms"] == 3000.0
    assert any("perf result_age_ms" in warning for warning in result["warnings"])


def test_launch_state_ready_when_perf_gate_passes(tmp_path):
    args = argparse.Namespace(host="192.168.42.1", udp_port=5000, control_port=5001)
    pipeline = {
        "enforced": True, "ok": True, "result_age_ms": 180.0, "detect_fps": 5.0,
        "checks": [{"name": "result_age_ms", "value": 180.0, "limit": 500, "ok": True}],
    }
    result = launch_demo._launch_state(
        tmp_path, args,
        url="http://127.0.0.1:8090/?token=t",
        local_url="http://127.0.0.1:8090/?token=t",
        token="t", port=8090, pid=123, session_dir=tmp_path / "s",
        peer="192.168.2.101",
        video_state={"implemented": True, "video": {"frames": 10}, "pipeline": pipeline},
        deploy_ok=True, warnings=[],
    )
    assert result["status"] == "ready"
    assert result["pipeline"]["ok"] is True


def test_deploy_requires_explicit_service_stop(tmp_path, monkeypatch):
    args = argparse.Namespace(
        host="192.168.42.1", no_deploy=False, verify=False,
        udp_port=5000, control_port=5001, allow_service_stop=False,
    )
    calls = []
    monkeypatch.setattr(launch_demo, "_run",
                        lambda cmd, **kwargs: (calls.append(cmd) or (0, "", "")))
    ok, warnings = launch_demo._deploy(tmp_path, args, "192.168.42.2")
    assert ok, warnings
    start = [cmd for cmd in calls if "start" in cmd][0]
    assert "--allow-service-stop" not in start


def test_deploy_passes_service_stop_when_authorized(tmp_path, monkeypatch):
    args = argparse.Namespace(
        host="192.168.42.1", no_deploy=False, verify=False,
        udp_port=5000, control_port=5001, allow_service_stop=True,
    )
    calls = []
    monkeypatch.setattr(launch_demo, "_run",
                        lambda cmd, **kwargs: (calls.append(cmd) or (0, "", "")))
    ok, warnings = launch_demo._deploy(tmp_path, args, "192.168.42.2")
    assert ok, warnings
    start = [cmd for cmd in calls if "start" in cmd][0]
    assert "--allow-service-stop" in start
