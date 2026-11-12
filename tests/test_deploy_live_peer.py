from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

import deploy_demo


def make_deployment(tmp_path: Path, runtime_args: list[str]) -> deploy_demo.Deployment:
    manifest = {
        "schema_version": 1,
        "name": "live_peer_demo",
        "version": "1.0.0",
        "device": {"model": "reCamera 2002", "os_min": "0.2.4"},
        "build": {"kind": "cpp", "binary": "live_peer_demo", "sources": ["main.c"]},
        "runtime": {
            "executable": "live_peer_demo",
            "args": runtime_args,
            "sudo": False,
        },
        "live": {"transport": "udp", "video": {"codec": "h264", "width": 1920, "height": 1080, "fps": 30}},
        "evaluation": {"task": "custom"},
    }
    args = argparse.Namespace(
        host="192.168.42.1", user="recamera", port=22,
        password_env="RECAMERA_PASSWORD", no_password=True,
        insecure_host_key=False, remote_dir=None,
    )
    return deploy_demo.Deployment(args, manifest, tmp_path)


def test_live_peer_is_injected_for_live_manifests(tmp_path, monkeypatch):
    deployment = make_deployment(tmp_path, [])
    monkeypatch.setenv("LIVE_PEER", "192.168.42.2")
    command, stdin = deployment._run_command(False, "/tmp/run.log", "/tmp/run.pid")
    assert stdin is None
    assert "--peer 192.168.42.2" in command
    assert "LIVE_PEER" not in command


def test_explicit_peer_wins_over_live_peer(tmp_path, monkeypatch):
    deployment = make_deployment(tmp_path, ["--peer", "10.0.0.9"])
    monkeypatch.setenv("LIVE_PEER", "192.168.42.2")
    command, _ = deployment._run_command(False, "/tmp/run.log", "/tmp/run.pid")
    assert "10.0.0.9" in command
    assert "192.168.42.2" not in command


def test_no_live_section_means_no_injection(tmp_path, monkeypatch):
    deployment = make_deployment(tmp_path, [])
    deployment.manifest.pop("live")
    monkeypatch.setenv("LIVE_PEER", "192.168.42.2")
    command, _ = deployment._run_command(False, "/tmp/run.log", "/tmp/run.pid")
    assert "192.168.42.2" not in command


def test_live_args_injected_for_live_manifests(tmp_path, monkeypatch):
    deployment = make_deployment(tmp_path, [])
    monkeypatch.setenv("LIVE_ARGS", "--port 5100 --control-port 5101")
    command, _ = deployment._run_command(False, "/tmp/run.log", "/tmp/run.pid")
    assert "--port 5100" in command
    assert "--control-port 5101" in command


def test_explicit_ports_win_over_live_args(tmp_path, monkeypatch):
    deployment = make_deployment(tmp_path, ["--port", "5200"])
    monkeypatch.setenv("LIVE_ARGS", "--port 5100 --control-port 5101")
    command, _ = deployment._run_command(False, "/tmp/run.log", "/tmp/run.pid")
    assert "--port 5200" in command
    assert "--port 5100" not in command
    assert "--control-port 5101" in command


def test_no_live_section_means_no_live_args(tmp_path, monkeypatch):
    deployment = make_deployment(tmp_path, [])
    deployment.manifest.pop("live")
    monkeypatch.setenv("LIVE_ARGS", "--port 5100 --control-port 5101")
    command, _ = deployment._run_command(False, "/tmp/run.log", "/tmp/run.pid")
    assert "--port 5100" not in command
    assert "--control-port 5101" not in command


def _start_deployment(tmp_path: Path, *, alive: bool) -> tuple[deploy_demo.Deployment, list[str]]:
    deployment = make_deployment(tmp_path, [])
    deployment.runtime["services_stop"] = ["S03node-red"]
    deployment.runtime["long_running"] = True
    deployment.args.allow_service_stop = True
    deployment.args.no_restore = False
    calls: list[str] = []
    deployment.stop_services = lambda: calls.append("stop")
    deployment.start_services = lambda: calls.append("start")
    deployment._run_command = lambda *args, **kwargs: ("true", None)
    deployment.pid_alive = lambda pidfile=None: alive
    deployment.tail_log = lambda path, lines=40: ""
    deployment.ssh.run = lambda *args, **kwargs: SimpleNamespace(
        ok=True, returncode=0, stdout="", stderr="")
    return deployment, calls


def test_failed_start_restores_services(tmp_path):
    deployment, calls = _start_deployment(tmp_path, alive=False)
    payload = deployment.start()
    assert payload["ok"] is False
    assert calls == ["stop", "start"]


def test_successful_start_keeps_services_stopped(tmp_path):
    deployment, calls = _start_deployment(tmp_path, alive=True)
    payload = deployment.start()
    assert payload["ok"] is True
    assert calls == ["stop"]


def test_failed_start_respects_no_restore(tmp_path):
    deployment, calls = _start_deployment(tmp_path, alive=False)
    deployment.args.no_restore = True
    payload = deployment.start()
    assert payload["ok"] is False
    assert calls == ["stop"]
