"""demo.json contract loading and offline validation.

The checks here never touch a device. They mirror ``references/demo-contract.md``
and are the single source of truth used by validate/build/deploy/package.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .schema import validate_schema
from .util import SKILL_ROOT, human_size, read_json, semver_tuple, sha256_file

SCHEMA_VERSION = 1
MANIFEST_NAMES = ("demo.json", "demo.manifest.json")

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
ENV_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
SHELL_UNSAFE_RE = re.compile(r"[;&|`$<>\n\r\0]")
HOST_PATH_RE = re.compile(
    r"(/(?:home|Users)/(?!recamera(?:/|$)|root(?:/|$))[A-Za-z0-9._-]+/"
    r"|[A-Za-z]:\\Users\\[^\\\s]+)"
)

REQUIRED_TOP = ("schema_version", "name", "version", "device", "build", "runtime", "evaluation")
VALID_BUILD_KINDS = ("cpp", "cmake", "prebuilt", "script")
VALID_SMOKE_KINDS = ("host-logic", "mock", "none")

# High-confidence secrets are errors; value assignments are warnings because
# documentation legitimately mentions variable names such as password_env.
SECRET_ERROR_RE = [
    (re.compile(r"sshpass\s+-p\s+['\"]?\S+"), "literal sshpass password"),
    (re.compile(r"BEGIN (?:RSA|OPENSSH|EC|DSA) PRIVATE KEY"), "private key material"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "GitHub token"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "API key"),
    (re.compile(
        r"(?i)\b(?:password|passwd|pwd)\s*[:=]\s*['\"][^'\"]{3,}['\"]"),
     "literal password assignment"),
    # Chinese "password" (mima) / "passphrase" (kouling) spelled with \uXXXX
    # escapes so this file stays ASCII while the detection behaviour is kept.
    (re.compile(
        r"(?:\u5BC6\u7801|\u53E3\u4EE4)\s*(?:[:\uFF1A=]\s*)?`(?!export\b|RECAMERA_PASSWORD\b|<|\$)[^`\n]{3,}`"),
     "literal password in backticks"),
]
SECRET_WARN_RE = [
    (re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|secret)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{12,}"),
     "literal credential"),
]


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        if not ok:
            self.error(detail or name)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "checks": self.checks,
            "unverified": self.unverified,
        }


def load_contracts(skill_root: Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    root = Path(skill_root or SKILL_ROOT)
    return (
        read_json(root / "configs" / "device-contract.json"),
        read_json(root / "configs" / "evaluation-contract.json"),
    )


def load_lock(name: str, skill_root: Path | None = None) -> dict[str, Any] | None:
    path = Path(skill_root or SKILL_ROOT) / "configs" / name
    if not path.is_file():
        return None
    return read_json(path)


def find_manifest(demo_dir: Path) -> Path:
    for name in MANIFEST_NAMES:
        candidate = Path(demo_dir) / name
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"no demo.json in {demo_dir}")


def load_manifest(demo_dir: Path) -> tuple[dict[str, Any], Path]:
    path = find_manifest(demo_dir)
    return read_json(path), path


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _text_files(demo_dir: Path, manifest: dict[str, Any], max_bytes: int = 1_000_000) -> Iterable[Path]:
    """Yield plausible text files that belong to the demo, excluding dist/eval."""
    skip_dirs = {".git", "dist", "build", "eval", "__pycache__", ".pytest_cache"}
    allowed = {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".py", ".sh", ".md", ".json",
               ".txt", ".cmake", ".mk", ".yaml", ".yml", ".ini", ".cfg", ".conf"}
    for path in Path(demo_dir).rglob("*"):
        if not path.is_file():
            continue
        if any(part in skip_dirs for part in path.relative_to(demo_dir).parts):
            continue
        if path.suffix.lower() not in allowed:
            continue
        try:
            if path.stat().st_size > max_bytes:
                continue
        except OSError:
            continue
        yield path


def scan_secrets(demo_dir: Path, manifest: dict[str, Any], result: ValidationResult) -> None:
    for path in _text_files(demo_dir, manifest):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = path.relative_to(demo_dir)
        for pattern, label in SECRET_ERROR_RE:
            if pattern.search(text):
                result.error(f"{rel}: {label}")
        for pattern, label in SECRET_WARN_RE:
            if pattern.search(text):
                result.warn(f"{rel}: possible {label}")
        if HOST_PATH_RE.search(text):
            result.warn(f"{rel}: contains a host-absolute path; use SDK env vars instead")


def validate_elf_riscv(path: Path, result: ValidationResult, label: str) -> bool:
    """Check ELF64 little-endian RISC-V (e_machine 243) without external tools."""
    try:
        with open(path, "rb") as fh:
            header = fh.read(64)
    except OSError as exc:
        result.error(f"{label}: cannot read {path}: {exc}")
        return False
    if len(header) < 20 or header[:4] != b"\x7fELF":
        result.error(f"{label}: not an ELF file: {path}")
        return False
    if header[4] != 2:
        result.error(f"{label}: not ELF64: {path}")
        return False
    machine = int.from_bytes(header[18:20], "little")
    if machine != 243:
        result.error(f"{label}: e_machine={machine}, expected 243 (RISC-V): {path}")
        return False
    return True


def _validate_schema(manifest: dict[str, Any], skill_root: Path | None, result: ValidationResult) -> None:
    """Run the JSON Schema when it ships with the skill. Missing schema is a warning."""
    path = Path(skill_root or SKILL_ROOT) / "schemas" / "demo.schema.json"
    if not path.is_file():
        result.warn("schemas/demo.schema.json missing; schema validation skipped")
        return
    try:
        schema = read_json(path)
    except (OSError, ValueError) as exc:
        result.warn(f"cannot read schemas/demo.schema.json: {exc}")
        return
    for error in validate_schema(manifest, schema):
        result.error(f"schema: {error}")


def _validate_smoke(manifest: dict[str, Any], demo_dir: Path, result: ValidationResult) -> None:
    smoke = manifest.get("smoke")
    if smoke is None:
        return
    if not isinstance(smoke, dict):
        result.error("smoke must be an object")
        return
    kind = smoke.get("kind")
    if kind not in VALID_SMOKE_KINDS:
        result.error(f"smoke.kind must be one of {VALID_SMOKE_KINDS}")
    if kind in ("host-logic", "mock"):
        command = smoke.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(c, str) for c in command):
            result.error("smoke.command must be a non-empty list of strings")
        elif any(SHELL_UNSAFE_RE.search(c) for c in command):
            result.error("smoke.command must not contain shell metacharacters; pass argv as a list")
    fixtures = smoke.get("fixtures", [])
    if not isinstance(fixtures, list) or not all(isinstance(f, str) for f in fixtures):
        result.error("smoke.fixtures must be a list of strings")
    else:
        for rel in fixtures:
            if not (demo_dir / rel).exists():
                result.error(f"smoke.fixtures entry missing: {rel}")
    timeout = smoke.get("timeout_s", 15)
    if not isinstance(timeout, int) or not 1 <= timeout <= 3600:
        result.error("smoke.timeout_s must be an integer in 1..3600")
    expected = smoke.get("expect_stdout", [])
    if not isinstance(expected, list) or not all(isinstance(e, str) for e in expected):
        result.error("smoke.expect_stdout must be a list of strings")


def _validate_evidence(manifest: dict[str, Any], result: ValidationResult) -> None:
    evidence = manifest.get("evidence", {})
    if not isinstance(evidence, dict):
        result.error("evidence must be an object")
        return
    stages = evidence.get("required_stages", [])
    allowed = {"static", "offline", "host-baseline", "device-upload", "device-run", "task-quality"}
    if not isinstance(stages, list) or not all(isinstance(s, str) for s in stages):
        result.error("evidence.required_stages must be a list of strings")
        return
    for stage in stages:
        if stage not in allowed:
            result.error(f"evidence.required_stages contains unknown stage: {stage!r}")


def validate_manifest(
    manifest: dict[str, Any],
    demo_dir: Path,
    contracts: tuple[dict[str, Any], dict[str, Any]],
    mode: str = "demo",
    skill_root: Path | None = None,
) -> ValidationResult:
    result = ValidationResult()
    device_contract, eval_contract = contracts
    demo_dir = Path(demo_dir)

    if not isinstance(manifest, dict):
        result.error("manifest must be a JSON object")
        return result

    _validate_schema(manifest, skill_root, result)

    for key in REQUIRED_TOP:
        if key not in manifest:
            result.error(f"missing required key: {key}")
    if result.errors:
        return result

    if manifest.get("schema_version") != SCHEMA_VERSION:
        result.error(f"schema_version must be {SCHEMA_VERSION}")

    name = manifest.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        result.error("name must match ^[a-z0-9][a-z0-9_-]{1,63}$")
    if not isinstance(manifest.get("version"), str) or not manifest["version"].strip():
        result.error("version must be a non-empty string")

    # Device contract
    device = manifest.get("device") or {}
    if not isinstance(device, dict):
        result.error("device must be an object")
        device = {}
    model = device.get("model")
    devices = (device_contract.get("devices") or {})
    if model not in devices:
        result.error(f"device.model {model!r} is not in device-contract devices: {sorted(devices)}")
    else:
        profile = devices[model]
        if device.get("arch", profile.get("arch")) != profile.get("arch"):
            result.error(f"device.arch must be {profile.get('arch')!r}")
        os_min = str(device.get("os_min", ""))
        tested = profile.get("os", {}).get("tested", [])
        if os_min and tested:
            newest = max(tested, key=semver_tuple)
            if semver_tuple(os_min) > semver_tuple(newest):
                result.warn(f"device.os_min {os_min} is newer than tested {newest}")

    # Build contract
    build = manifest.get("build") or {}
    if not isinstance(build, dict):
        result.error("build must be an object")
        build = {}
    kind = build.get("kind")
    if kind not in VALID_BUILD_KINDS:
        result.error(f"build.kind must be one of {VALID_BUILD_KINDS}")
    else:
        source_dir = demo_dir / str(build.get("source_dir", "."))
        if kind == "cpp":
            sources = build.get("sources")
            if not isinstance(sources, list) or not sources:
                result.error("build.sources must be a non-empty list for kind=cpp")
            else:
                for rel in sources:
                    if not (source_dir / rel).is_file():
                        result.error(f"build source missing: {source_dir / rel}")
            if not build.get("binary"):
                result.error("build.binary is required for kind=cpp")
        elif kind == "cmake":
            if not source_dir.is_dir():
                result.error(f"build.source_dir missing: {source_dir}")
            if not build.get("binary"):
                result.error("build.binary is required for kind=cmake")
        elif kind == "prebuilt":
            artifact = build.get("artifact")
            if not artifact or not (demo_dir / artifact).is_file():
                result.error(f"build.artifact missing: {artifact}")
            elif build.get("sha256") and sha256_file(demo_dir / artifact) != build["sha256"]:
                result.error("build.artifact sha256 mismatch")
            if not build.get("binary"):
                result.error("build.binary is required for kind=prebuilt")
        elif kind == "script":
            script = build.get("script")
            if not script or not (demo_dir / script).is_file():
                result.error(f"build.script missing: {script}")

    for key in ("include_dirs", "lib_dirs", "libs", "cflags", "ldflags", "defines"):
        value = build.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            result.error(f"build.{key} must be a list of strings")

    # Runtime contract
    runtime = manifest.get("runtime") or {}
    if not isinstance(runtime, dict):
        result.error("runtime must be an object")
        runtime = {}
    executable = runtime.get("executable")
    if not isinstance(executable, str) or not executable or "/" in executable:
        result.error("runtime.executable must be a relative file name")
    elif kind in ("cpp", "cmake", "prebuilt") and build.get("binary") and executable != build.get("binary"):
        result.warn(f"runtime.executable {executable!r} differs from build.binary {build.get('binary')!r}")

    args = runtime.get("args", [])
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        result.error("runtime.args must be a list of strings")
    else:
        for arg in args:
            if SHELL_UNSAFE_RE.search(arg):
                result.error(f"runtime.args contains a shell metacharacter: {arg!r}")

    env = runtime.get("env", {})
    if not isinstance(env, dict):
        result.error("runtime.env must be an object")
    else:
        for key, value in env.items():
            if not ENV_KEY_RE.match(str(key)):
                result.error(f"runtime.env key is invalid: {key!r}")
            if not isinstance(value, str) or "\n" in value or "\0" in value:
                result.error(f"runtime.env[{key!r}] must be a single-line string")

    if not isinstance(runtime.get("sudo", False), bool):
        result.error("runtime.sudo must be a boolean")
    if not isinstance(runtime.get("long_running", False), bool):
        result.error("runtime.long_running must be a boolean")
    files = runtime.get("files", [])
    if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
        result.error("runtime.files must be a list of strings")
    else:
        for rel in files:
            if not (demo_dir / rel).is_file():
                result.error(f"runtime.files entry missing: {rel}")
    if not isinstance(runtime.get("requires_camera", False), bool):
        result.error("runtime.requires_camera must be a boolean")
    timeout = runtime.get("timeout_s", 30)
    if not isinstance(timeout, int) or not 1 <= timeout <= 3600:
        result.error("runtime.timeout_s must be an integer in 1..3600")

    allowed_services = set((device_contract.get("allowed_service_controls") or {}).values())
    for key in ("services_stop", "services_start"):
        services = runtime.get(key, [])
        if not isinstance(services, list) or not all(isinstance(s, str) for s in services):
            result.error(f"runtime.{key} must be a list of strings")
            continue
        for svc in services:
            if svc not in allowed_services:
                result.error(f"runtime.{key}: service {svc!r} is not an allowed service control")
    if runtime.get("services_stop") and not runtime.get("requires_camera"):
        result.warn("services_stop is declared but runtime.requires_camera is false")

    expected = runtime.get("expect_stdout", [])
    if not isinstance(expected, list) or not all(isinstance(s, str) for s in expected):
        result.error("runtime.expect_stdout must be a list of strings")
    if mode in ("deliver", "publish") and not expected:
        result.warn("runtime.expect_stdout is empty; device verification will have no acceptance marker")

    # Evaluation contract
    evaluation = manifest.get("evaluation") or {}
    if not isinstance(evaluation, dict):
        result.error("evaluation must be an object")
        evaluation = {}
    task = evaluation.get("task")
    tasks = (eval_contract.get("tasks") or {})
    if task not in tasks:
        result.error(f"evaluation.task {task!r} is not in evaluation-contract tasks: {sorted(tasks)}")
    thresholds = evaluation.get("thresholds", {})
    if not isinstance(thresholds, dict):
        result.error("evaluation.thresholds must be an object")
    else:
        for key, value in thresholds.items():
            if not _is_number(value) or not 0 <= value <= 1:
                result.error(f"evaluation.thresholds[{key!r}] must be a number in 0..1")
    if mode in ("deliver", "publish") and task in ("detection", "classification", "ocr", "segmentation", "pose") and not thresholds:
        result.warn("evaluation.thresholds is empty; contract defaults will be used")

    # Model/artifact contract
    model_spec = manifest.get("model")
    if model_spec is not None:
        if not isinstance(model_spec, dict):
            result.error("model must be an object or null")
        else:
            model_file = model_spec.get("file")
            if not model_file or not (demo_dir / model_file).is_file():
                result.error(f"model.file missing: {model_file}")
            else:
                path = demo_dir / model_file
                expected_sha = model_spec.get("sha256")
                if expected_sha:
                    actual = sha256_file(path)
                    if actual != expected_sha:
                        result.error(f"model sha256 mismatch: expected {expected_sha}, got {actual}")
                elif mode in ("deliver", "publish"):
                    result.error("model.sha256 is required for deliver mode")
                if not model_spec.get("task"):
                    result.warn("model.task is empty")
                if model_spec.get("conversion") and mode in ("deliver", "publish"):
                    conv = model_spec["conversion"]
                    for field_name in ("onnx_sha256", "dataset", "input_size", "layout"):
                        if not conv.get(field_name):
                            result.warn(f"model.conversion.{field_name} is empty")

    # Inputs contract
    inputs = manifest.get("inputs", {})
    if not isinstance(inputs, dict):
        result.error("inputs must be an object")
    else:
        for key in ("camera", "video", "images"):
            if key in inputs and not isinstance(inputs[key], bool):
                result.error(f"inputs.{key} must be a boolean")

    _validate_smoke(manifest, demo_dir, result)
    _validate_evidence(manifest, result)

    # Publish mode adds distribution hygiene on top of deliver.
    if mode == "publish":
        if manifest.get("version") in ("1.0.0", "0.0.0"):
            result.warn("publish mode: version looks like a placeholder")
        if not (demo_dir / "README.md").is_file():
            result.error("publish mode requires README.md")
        if model_spec and not model_spec.get("conversion"):
            result.error("publish mode requires model.conversion provenance")
        if not (demo_dir / "LICENSE").is_file():
            result.warn("publish mode: no LICENSE file")

    scan_secrets(demo_dir, manifest, result)

    # A live demo without thresholds cannot be judged "performance OK".
    live = manifest.get("live")
    if isinstance(live, dict) and live.get("implemented") is True:
        perf = live.get("perf")
        if not isinstance(perf, dict) or not perf:
            result.warn(
                "live.perf is missing; launch cannot verify result age / update rate")

    # A built artifact, when present, must be the right architecture.
    dist_binary = demo_dir / "dist" / str(build.get("binary", ""))
    if build.get("binary") and dist_binary.is_file():
        validate_elf_riscv(dist_binary, result, "dist binary")
        result.checks.append({
            "name": "artifact",
            "ok": True,
            "detail": f"{dist_binary} {human_size(dist_binary.stat().st_size)} sha256={sha256_file(dist_binary)}",
        })
    elif mode in ("deliver", "publish") and kind in ("cpp", "cmake", "prebuilt"):
        result.warn(f"built artifact not found: {dist_binary} (run build_demo.py first)")

    result.unverified.extend([
        "device runtime availability and permissions",
        "camera/NPU arbitration with running services",
        "model numerical accuracy against the documented baseline",
    ])
    return result


def print_result(result: ValidationResult, title: str = "result") -> None:
    print(f"{title}: {'PASS' if result.ok else 'FAIL'}")
    for warning in result.warnings:
        print(f"  WARN  {warning}")
    for error in result.errors:
        print(f"  ERROR {error}")
    for item in result.unverified:
        print(f"  UNVERIFIED {item}")