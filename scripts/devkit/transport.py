"""ssh/scp transport.

Password authentication prefers OpenSSH's ``SSH_ASKPASS`` with
``SSH_ASKPASS_REQUIRE=force`` (no local tty required). The askpass helper reads
the password from the child environment, never from a file or argv.
Set ``DEMO_SSH_AUTH=sshpass`` to force the ``sshpass -e`` path.

This module is the only place that touches a device. Import it lazily in
commands that must stay offline.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .util import redact


class SSHError(RuntimeError):
    pass


@dataclass
class RemoteResult:
    command: str
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class SSH:
    """Thin ssh/scp wrapper that never puts a password in argv."""

    _askpass_dir: str | None = None

    def __init__(
        self,
        host: str,
        user: str = "recamera",
        port: int = 22,
        password_env: str | None = None,
        strict_host_key: str = "accept-new",
        connect_timeout: int = 10,
    ) -> None:
        self.host = host
        self.user = user
        self.port = int(port)
        self.password_env = password_env
        self.password = os.environ.get(password_env) if password_env else None
        self.strict_host_key = strict_host_key
        self.connect_timeout = connect_timeout
        self.auth_mode = os.environ.get("DEMO_SSH_AUTH", "askpass").lower()
        if self.auth_mode == "auto":
            self.auth_mode = "sshpass" if shutil.which("sshpass") else "askpass"
        if os.name == "nt" and self.auth_mode == "askpass" and shutil.which("sshpass"):
            self.auth_mode = "sshpass"

    def _auth_prefix(self) -> list[str]:
        return ["sshpass", "-e"] if self.password and self.auth_mode == "sshpass" else []

    def _askpass_script(self) -> str:
        if SSH._askpass_dir is None:
            dirname = tempfile.mkdtemp(prefix="demo-askpass-")
            os.chmod(dirname, 0o700)
            script = os.path.join(dirname, "askpass.sh")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\nprintf '%s\\n' \"$RECAMERA_ASKPASS_PASSWORD\"\n")
            os.chmod(script, 0o700)
            SSH._askpass_dir = dirname
        return os.path.join(SSH._askpass_dir, "askpass.sh")

    def _needs_askpass(self) -> bool:
        return bool(self.password) and self.auth_mode != "sshpass"

    def _wrap_setsid(self, cmd: list[str]) -> list[str]:
        return ["setsid", "-w", *cmd] if shutil.which("setsid") else cmd

    def _common_opts(self) -> list[str]:
        opts = [
            "-o", f"ConnectTimeout={self.connect_timeout}",
            "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=3",
        ]
        if self.strict_host_key == "insecure":
            opts += ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"]
        else:
            opts += ["-o", f"StrictHostKeyChecking={self.strict_host_key}"]
        if not self.password:
            opts += ["-o", "BatchMode=yes"]
        else:
            # Do not offer local agent keys first: a device that rate-limits
            # failed publickey attempts can reject a valid password login.
            opts += ["-o", "PreferredAuthentications=password",
                     "-o", "PubkeyAuthentication=no",
                     "-o", "IdentitiesOnly=yes"]
        return opts

    def _auth_strict_opts(self) -> list[str]:
        opts: list[str] = []
        if self.strict_host_key == "insecure":
            opts += ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"]
        else:
            opts += ["-o", f"StrictHostKeyChecking={self.strict_host_key}"]
        if self.password:
            opts += ["-o", "PreferredAuthentications=password",
                     "-o", "PubkeyAuthentication=no",
                     "-o", "IdentitiesOnly=yes"]
        return opts

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.pop("SSHPASS", None)
        env.pop("RECAMERA_ASKPASS_PASSWORD", None)
        if self.password and self.auth_mode == "sshpass":
            env["SSHPASS"] = self.password
        elif self._needs_askpass():
            if os.name == "nt":
                raise SSHError(
                    "Windows OpenSSH cannot execute the POSIX SSH_ASKPASS helper; "
                    "use an SSH key, or install sshpass and set DEMO_SSH_AUTH=sshpass")
            env["SSH_ASKPASS"] = self._askpass_script()
            env["SSH_ASKPASS_REQUIRE"] = "force"
            env["DISPLAY"] = env.get("DISPLAY", ":0")
            env["RECAMERA_ASKPASS_PASSWORD"] = self.password
        return env

    def run(self, command: str, timeout: int = 60, stdin: str | None = None) -> RemoteResult:
        cmd = self._wrap_setsid(self._auth_prefix() + ["ssh"] + self._common_opts() + ["-p", str(self.port),
               f"{self.user}@{self.host}", command])
        kwargs = {"capture_output": True, "text": True, "timeout": timeout, "env": self._env()}
        if stdin is None:
            kwargs["stdin"] = subprocess.DEVNULL
        else:
            kwargs["input"] = stdin
        try:
            proc = subprocess.run(cmd, **kwargs)
        except FileNotFoundError as exc:
            raise SSHError(f"ssh/sshpass not available: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise SSHError(f"ssh timed out after {timeout}s: {command[:120]}") from exc
        return RemoteResult(command, proc.returncode, proc.stdout, redact(proc.stderr, self.password))

    def check(self, command: str, timeout: int = 60, stdin: str | None = None) -> RemoteResult:
        result = self.run(command, timeout=timeout, stdin=stdin)
        if not result.ok:
            raise SSHError(f"remote command failed ({result.returncode}): {command[:200]}\n{result.stderr}")
        return result

    def put(self, local: Path, remote: str, recursive: bool = False, timeout: int = 300) -> None:
        cmd = self._wrap_setsid(self._auth_prefix() + ["scp"] + ["-P", str(self.port)])
        if recursive:
            cmd.append("-r")
        cmd += self._auth_strict_opts()
        cmd += [str(local), f"{self.user}@{self.host}:{remote}"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                                  env=self._env(), stdin=subprocess.DEVNULL)
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise SSHError(f"scp failed: {exc}") from exc
        if proc.returncode != 0:
            raise SSHError(f"scp failed: {redact(proc.stderr, self.password)}")

    def get(self, remote: str, local: Path, recursive: bool = False, timeout: int = 300) -> None:
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        cmd = self._wrap_setsid(self._auth_prefix() + ["scp"] + ["-P", str(self.port)])
        if recursive:
            cmd.append("-r")
        cmd += self._auth_strict_opts()
        cmd += [f"{self.user}@{self.host}:{remote}", str(local)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                                  env=self._env(), stdin=subprocess.DEVNULL)
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise SSHError(f"scp failed: {exc}") from exc
        if proc.returncode != 0:
            raise SSHError(f"scp failed: {redact(proc.stderr, self.password)}")


def remote_sudo_command(command: str, password: str | None) -> tuple[str, str | None]:
    """Return (remote command, stdin) using sudo -S without exposing the password."""
    if password:
        return f"sudo -S -p '' sh -c {shlex.quote(command)}", password + "\n"
    return f"sudo -n sh -c {shlex.quote(command)}", None