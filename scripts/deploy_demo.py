#!/usr/bin/env python3
"""Upload, install, run and verify a reCamera demo over SSH.

Actions are explicit: upload (stage only), install, start, stop, restart,
status, verify, collect, clean. File transfer is SHA-256 verified. Services are
only touched when the manifest declares them AND --allow-service-stop is given.
"""

from __future__ import annotations

import argparse
import os
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from demo_lib import (  # noqa: E402
    SSH,
    SSHError,
    STAGES,
    load_contracts,
    load_manifest,
    now_iso,
    remote_sudo_command,
    sha256_file,
    validate_manifest,
    write_json,
)


def q(value: str | Path) -> str:
    return shlex.quote(str(value))


class Deployment:
    def __init__(self, args: argparse.Namespace, manifest: dict, demo_dir: Path):
        self.args = args
        self.manifest = manifest
        self.demo_dir = demo_dir
        self.name = manifest["name"]
        self.runtime = manifest.get("runtime", {})
        self.device_contract, _ = load_contracts()
        self.profile = self.device_contract["devices"][manifest["device"]["model"]]
        self.remote_dir = args.remote_dir or f"{self.profile['paths']['demo_root']}/{self.name}"
        self.stage_root = self.profile["paths"]["staging_root"]
        demo_root = self.profile["paths"]["demo_root"].rstrip("/") + "/"
        if not self.remote_dir.startswith(demo_root):
            raise ValueError(f"remote_dir must stay under {demo_root}: {self.remote_dir}")
        self.ssh = SSH(
            args.host, args.user, args.port,
            None if args.no_password else args.password_env,
            strict_host_key="insecure" if args.insecure_host_key else "accept-new",
        )
        self.password = self.ssh.password
        self.checks: list[dict] = []
        self.files: list[dict] = []

    # ---- helpers ---------------------------------------------------------
    def add_check(self, name: str, ok: bool, detail: str, severity: str = "error") -> None:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail, "severity": severity})

    def sudo(self, command: str, timeout: int = 60):
        remote, stdin = remote_sudo_command(command, self.password)
        return self.ssh.run(remote, timeout=timeout, stdin=stdin)

    def local_files(self) -> list[tuple[Path, str]]:
        build = self.manifest["build"]
        binary = self.demo_dir / "dist" / build["binary"]
        if not binary.is_file():
            raise FileNotFoundError(f"built artifact missing: {binary} (run build_demo.py)")
        pairs: list[tuple[Path, str]] = [(binary, build["binary"])]
        model = self.manifest.get("model")
        if model and model.get("file"):
            pairs.append((self.demo_dir / model["file"], model["file"]))
        for rel in self.runtime.get("files", []):
            pairs.append((self.demo_dir / rel, rel))
        pairs.append((self.demo_dir / "demo.json", "demo.json"))
        # de-duplicate by destination, keeping order
        seen: set[str] = set()
        unique: list[tuple[Path, str]] = []
        for src, dst in pairs:
            if dst in seen:
                continue
            if not src.is_file():
                raise FileNotFoundError(f"declared file missing: {src}")
            seen.add(dst)
            unique.append((src, dst))
        return unique

    def remote_hashes(self, remote_dir: str, names: list[str]) -> dict[str, str]:
        listing = " ".join(q(n) for n in names)
        result = self.ssh.run(f"cd {q(remote_dir)} && sha256sum {listing}", timeout=60)
        if not result.ok:
            return {}
        hashes: dict[str, str] = {}
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2:
                hashes[parts[1].lstrip("*")] = parts[0]
        return hashes

    def tail_log(self, path: str, lines: int = 40) -> str:
        command = f"tail -n {int(lines)} {q(path)} 2>/dev/null"
        if self.runtime.get("sudo", False):
            result = self.sudo(command, timeout=30)
        else:
            result = self.ssh.run(command, timeout=30)
        return result.stdout

    def service_running(self, pattern: str) -> bool:
        result = self.ssh.run("ps w 2>/dev/null", timeout=30)
        return any(pattern in line and "grep" not in line for line in result.stdout.splitlines())

    def service_names(self) -> list[str]:
        return list(self.runtime.get("services_stop", []))

    def stop_services(self) -> None:
        for service in self.service_names():
            cmd = f"/etc/init.d/{service} stop"
            result = self.sudo(cmd, timeout=45)
            self.add_check(f"service.{service}", result.ok,
                           f"{service} stop rc={result.returncode} {result.stderr.strip()[:120]}")

    def start_services(self) -> None:
        services = list(dict.fromkeys([
            *self.runtime.get("services_stop", []),
            *self.runtime.get("services_start", []),
        ]))
        for service in services:
            cmd = f"/etc/init.d/{service} start"
            result = self.sudo(cmd, timeout=60)
            self.add_check(f"service.{service}.start", result.ok,
                           f"{service} start rc={result.returncode} {result.stderr.strip()[:120]}")

    # ---- actions ---------------------------------------------------------
    def upload(self) -> str:
        pairs = self.local_files()
        stage = f"{self.stage_root}/{self.name}-{now_iso().replace(':', '').replace('-', '')}"
        result = self.ssh.run(f"mkdir -p {q(stage)}", timeout=30)
        if not result.ok:
            raise SSHError(f"cannot create staging directory: {result.stderr}")
        for src, rel in pairs:
            remote = f"{stage}/{rel}"
            parent = str(Path(remote).parent)
            self.ssh.run(f"mkdir -p {q(parent)}", timeout=30)
            self.ssh.put(src, remote, timeout=600)
            self.files.append({"path": rel, "size": src.stat().st_size,
                               "sha256": sha256_file(src), "destination": remote})
        remote_hashes = self.remote_hashes(stage, [rel for _, rel in pairs])
        for item in self.files:
            if remote_hashes.get(item["path"]) != item["sha256"]:
                raise SSHError(f"remote SHA-256 mismatch for {item['path']}")
        self.add_check("upload.sha256", True, f"{len(self.files)} files verified in {stage}")
        return stage

    def install(self) -> str:
        stage = self.upload()
        exists = self.ssh.run(f"[ -d {q(self.remote_dir)} ] && echo yes || echo no", timeout=30)
        if exists.stdout.strip() == "yes":
            if not self.args.replace:
                raise SSHError(f"{self.remote_dir} already exists; pass --replace to back it up and replace it")
            backup = f"{self.remote_dir}.bak-{now_iso().replace(':', '').replace('-', '')}"
            result = self.ssh.run(f"mv {q(self.remote_dir)} {q(backup)}", timeout=30)
            if not result.ok:
                raise SSHError(f"backup failed: {result.stderr}")
            self.add_check("install.backup", True, f"previous install moved to {backup}")
        result = self.ssh.run(
            f"mkdir -p {q(self.remote_dir)} && cp -a {q(stage)}/. {q(self.remote_dir)}/ && "
            f"chmod +x {q(self.remote_dir + '/' + self.manifest['build']['binary'])}",
            timeout=60,
        )
        if not result.ok:
            raise SSHError(f"install failed: {result.stderr}")
        installed = self.remote_hashes(self.remote_dir, [rel for _, rel in self.local_files()])
        mismatched = [rel for _, rel in self.local_files()
                      if installed.get(rel) != next(f["sha256"] for f in self.files if f["path"] == rel)]
        self.add_check("install.sha256", not mismatched,
                       f"installed files verified in {self.remote_dir}; mismatched={mismatched}")
        if mismatched:
            raise SSHError(f"installed SHA-256 mismatch: {mismatched}")
        if self.manifest.get("live"):
            # Keep one rollback slot; repeated live launches must not fill /userdata.
            prune = (f"ls -dt {q(self.remote_dir)}.bak-* 2>/dev/null | tail -n +2 | "
                     f"while read d; do rm -rf \"$d\"; done")
            self.ssh.run(prune, timeout=60)
        return self.remote_dir

    def _env_string(self) -> str:
        env = dict(self.profile.get("runtime_env", {}))
        env.update(self.runtime.get("env", {}))
        return " ".join(f"{k}={q(str(v))}" for k, v in env.items())

    def _run_command(self, background: bool, log: str, pidfile: str) -> tuple[str, str | None]:
        exe = self.remote_dir + "/" + self.runtime["executable"]
        runtime_args = list(self.runtime.get("args", []))
        live_peer = os.environ.get("LIVE_PEER", "").strip()
        if live_peer and self.manifest.get("live") and "--peer" not in runtime_args:
            runtime_args += ["--peer", live_peer]
        live_args = os.environ.get("LIVE_ARGS", "").strip()
        if live_args and self.manifest.get("live"):
            try:
                extra = shlex.split(live_args)
            except ValueError:
                extra = []
            for index in range(0, len(extra) - 1, 2):
                flag, value = extra[index], extra[index + 1]
                if flag in ("--port", "--control-port") and flag not in runtime_args:
                    runtime_args += [flag, value]
        args = " ".join(q(a) for a in runtime_args)
        env = self._env_string()
        inner = f"cd {q(self.remote_dir)} && "
        if background:
            inner += (f"setsid env {env} {q(exe)} {args} > {q(log)} 2>&1 < /dev/null & "
                      f"echo $! > {q(pidfile)}")
        else:
            inner += f"env {env} {q(exe)} {args} > {q(log)} 2>&1"
        if self.runtime.get("sudo", False):
            return remote_sudo_command(inner, self.password)
        return inner, None

    def start(self) -> dict:
        if self.runtime.get("services_stop") and not self.args.allow_service_stop:
            self.add_check("services.authorization", False,
                           "manifest requires stopping services; pass --allow-service-stop explicitly")
            return {"ok": False}
        services_stopped = False
        if self.runtime.get("services_stop"):
            self.stop_services()
            services_stopped = True
        try:
            self.ssh.run(f"mkdir -p {q(self.remote_dir + '/logs')}", timeout=30)
            stamp = now_iso().replace(":", "").replace("-", "")
            log = f"{self.remote_dir}/logs/run-{stamp}.log"
            pidfile = f"{self.remote_dir}/run.pid"
            long_running = bool(self.runtime.get("long_running", False))
            command, stdin = self._run_command(long_running, log, pidfile)
            timeout = self.runtime.get("timeout_s", 30) if not long_running else 30
            result = self.ssh.run(command, timeout=timeout, stdin=stdin)
            if self.runtime.get("sudo", False):
                # The sudo child creates root-owned logs; make them readable/collectable.
                self.sudo(f"chmod 644 {q(log)} {q(pidfile)} 2>/dev/null || true", timeout=30)
            self.add_check("run.command", result.ok or not long_running,
                           f"run rc={result.returncode}", severity="error" if not long_running else "warn")
            if long_running:
                import time
                time.sleep(2)
                alive = self.pid_alive(pidfile)
                self.add_check("run.pid", alive, f"pid file {pidfile} is {'alive' if alive else 'dead'}")
                if not alive:
                    self._restore_services_after_failure(services_stopped)
                log_tail = self.tail_log(log)
                return {"ok": alive, "background": True, "pidfile": pidfile, "log": log,
                        "stdout": log_tail, "returncode": result.returncode}
            if not result.ok:
                self._restore_services_after_failure(services_stopped)
            log_tail = self.tail_log(log) if log else ""
            stdout = log_tail or result.stdout
            return {"ok": result.ok, "background": False, "returncode": result.returncode,
                    "stdout": stdout, "stderr": result.stderr, "log": log}
        except Exception:
            self._restore_services_after_failure(services_stopped)
            raise

    def _restore_services_after_failure(self, services_stopped: bool) -> None:
        """Restart services a failed start() stopped, unless explicitly disabled.

        A successful start must leave services stopped (the demo owns the camera);
        only the failure paths restore them.
        """
        if not services_stopped or getattr(self.args, "no_restore", False):
            return
        self.start_services()

    def pid_alive(self, pidfile: str | None = None) -> bool:
        pidfile = pidfile or f"{self.remote_dir}/run.pid"
        command = (f"if [ -s {q(pidfile)} ]; then pid=$(cat {q(pidfile)}); "
                   f"kill -0 \"$pid\" 2>/dev/null && echo alive || echo dead; else echo missing; fi")
        if self.runtime.get("sudo", False):
            result = self.sudo(command, timeout=30)
        else:
            result = self.ssh.run(command, timeout=30)
        return result.stdout.strip().endswith("alive")

    def stop(self) -> dict:
        pidfile = f"{self.remote_dir}/run.pid"
        command = (f"if [ -s {q(pidfile)} ]; then pid=$(cat {q(pidfile)}); "
                   f"kill \"$pid\" 2>/dev/null; sleep 1; kill -0 \"$pid\" 2>/dev/null && echo alive || echo stopped; "
                   f"rm -f {q(pidfile)}; else echo missing; fi")
        result = self.sudo(command, timeout=30) if self.runtime.get("sudo", False) else self.ssh.run(command, timeout=30)
        stopped = result.stdout.strip().endswith(("stopped", "missing"))
        self.add_check("stop", stopped, f"stop result: {result.stdout.strip() or result.stderr.strip()[:120]}")
        if not self.args.no_restore and self.service_names():
            self.start_services()
        return {"ok": stopped, "stdout": result.stdout.strip()}

    def status(self) -> dict:
        pairs = self.local_files()
        local = {rel: sha256_file(src) for src, rel in pairs}
        installed = self.remote_hashes(self.remote_dir, [rel for _, rel in pairs])
        mismatched = [rel for rel, digest in local.items() if installed.get(rel) != digest]
        alive = self.pid_alive()
        log = self.ssh.run(f"ls -t {q(self.remote_dir + '/logs')}/*.log 2>/dev/null | head -1", timeout=30).stdout.strip()
        tail = self.tail_log(log) if log else ""
        self.add_check("status.files", installed and not mismatched,
                       f"installed={len(installed)} mismatched={mismatched}")
        self.add_check("status.pid", alive, "demo process is not running", severity="warn")
        return {"ok": bool(installed) and not mismatched, "pid_alive": alive,
                "installed": installed, "mismatched": mismatched, "log": log, "log_tail": tail}

    def verify(self) -> dict:
        status = self.status()
        log_text = status.get("log_tail", "")
        markers = self.runtime.get("expect_stdout", [])
        missing = [m for m in markers if m not in log_text]
        long_running = bool(self.runtime.get("long_running", False))
        markers_ok = not missing
        passed = status["ok"] and (status["pid_alive"] if long_running else markers_ok)
        self.add_check("verify.markers", not missing, f"missing stdout markers: {missing}")
        live = self.manifest.get("live")
        if isinstance(live, dict) and not bool(live.get("implemented", False)):
            self.add_check("live.implemented", False,
                           "live.implemented=false: verify only proves process/log, not camera/video",
                           severity="warn")
        return {
            "ok": passed,
            "running": status["pid_alive"],
            "files_ok": status["ok"],
            "missing_markers": missing,
            "log": status.get("log"),
            "log_tail": log_text,
            "evidence_level": "device-run",
        }

    def collect(self) -> dict:
        local_dir = self.demo_dir / "dist" / "device"
        local_dir.mkdir(parents=True, exist_ok=True)
        remote_logs = self.remote_dir + "/logs"
        try:
            self.ssh.get(remote_logs, local_dir / "logs", recursive=True, timeout=300)
            got = sorted(p.name for p in (local_dir / "logs").glob("*"))
        except SSHError as exc:
            got = []
            self.add_check("collect.logs", False, f"log collection failed: {exc}")
        manifest_copy = self.ssh.run(f"cat {q(self.remote_dir + '/demo.json')}", timeout=30)
        if manifest_copy.ok:
            (local_dir / "demo.json").write_text(manifest_copy.stdout, encoding="utf-8")
        self.add_check("collect", bool(got) or manifest_copy.ok, f"collected {len(got)} log file(s)")
        return {"ok": bool(got) or manifest_copy.ok, "local_dir": str(local_dir), "logs": got}

    def clean(self) -> dict:
        if not self.args.yes:
            raise SSHError("clean removes the remote demo directory; pass --yes")
        result = self.ssh.run(
            f"rm -rf {q(self.remote_dir)} {q(self.remote_dir)}.bak-* "
            f"{q(self.stage_root)}/{q(self.name)}-*", timeout=30)
        self.add_check("clean", result.ok, f"removed {self.remote_dir} and staged uploads" if result.ok else result.stderr)
        return {"ok": result.ok}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo-dir", required=True, type=Path)
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", default="recamera")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--password-env", default="RECAMERA_PASSWORD")
    parser.add_argument("--no-password", action="store_true")
    parser.add_argument("--insecure-host-key", action="store_true")
    parser.add_argument("--remote-dir")
    parser.add_argument("--action", required=True,
                        choices=("upload", "install", "start", "stop", "restart", "status",
                                 "verify", "collect", "clean"))
    parser.add_argument("--replace", action="store_true", help="back up and replace an existing install")
    parser.add_argument("--allow-service-stop", action="store_true")
    parser.add_argument("--no-restore", action="store_true", help="do not restart services on stop")
    parser.add_argument("--yes", action="store_true", help="confirm destructive clean")
    parser.add_argument("--skip-validate", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    demo_dir = args.demo_dir.resolve()
    try:
        manifest, _ = load_manifest(demo_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not args.skip_validate:
        from demo_lib import ValidationResult, print_result
        result = validate_manifest(manifest, demo_dir, load_contracts(), mode="deliver")
        print_result(result, "pre-deploy validation")
        if not result.ok:
            return 1

    deployment = Deployment(args, manifest, demo_dir)
    payload: dict
    try:
        if args.action == "upload":
            stage = deployment.upload()
            payload = {"ok": True, "stage": stage}
        elif args.action == "install":
            remote_dir = deployment.install()
            payload = {"ok": True, "remote_dir": remote_dir}
        elif args.action == "start":
            payload = deployment.start()
        elif args.action == "stop":
            payload = deployment.stop()
        elif args.action == "restart":
            args.no_restore = True
            stopped = deployment.stop()
            payload = deployment.start()
            payload["stop"] = stopped
        elif args.action == "status":
            payload = deployment.status()
        elif args.action == "verify":
            payload = deployment.verify()
        elif args.action == "collect":
            payload = deployment.collect()
        elif args.action == "clean":
            payload = deployment.clean()
        else:
            raise AssertionError(args.action)
    except (SSHError, FileNotFoundError) as exc:
        payload = {"ok": False, "error": str(exc)}
        print(f"ERROR: {exc}", file=sys.stderr)

    deploy_stage = {
        "upload": "device-upload",
        "install": "device-upload",
        "verify": "device-run",
    }.get(args.action, "device-run")
    report = {
        "schema_version": 1,
        "kind": "demo-deploy",
        "generated_at": now_iso(),
        "action": args.action,
        "demo": manifest.get("name"),
        "target": {"host": args.host, "user": args.user, "port": args.port},
        "remote_dir": deployment.remote_dir,
        "files": deployment.files,
        "checks": deployment.checks,
        "payload": payload,
        "ok": bool(payload.get("ok")),
        "stages": {
            stage: {"status": (
                ("passed" if payload.get("ok") else "failed") if stage == deploy_stage
                else "passed" if stage == "device-upload" and args.action == "verify" and payload.get("ok")
                else "not_run"
            )}
            for stage in STAGES
        },
    }
    if args.report:
        write_json(args.report, report)
    else:
        default = demo_dir / "dist" / "device" / f"{args.action}-report.json"
        write_json(default, report)
        print(f"report: {default}")

    if payload.get("stdout"):
        print("--- device output ---")
        print(payload["stdout"].rstrip())
    if payload.get("log_tail") and not payload.get("stdout"):
        print("--- log tail ---")
        print(payload["log_tail"].rstrip())
    print(f"deploy {args.action}: {'PASS' if report['ok'] else 'FAIL'}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())