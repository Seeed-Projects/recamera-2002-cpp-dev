"""Compatibility facade for the reCamera demo toolkit.

The implementation lives in :mod:`devkit`. Existing scripts and tests import
from ``demo_lib``; this module keeps that surface stable while the toolkit is
split into focused modules.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from devkit.artifacts import (  # noqa: F401,E402
    compiler_path,
    deterministic_tar,
    find_toolchain,
    package_files,
)
from devkit.contracts import (  # noqa: F401,E402
    ENV_KEY_RE,
    MANIFEST_NAMES,
    NAME_RE,
    REQUIRED_TOP,
    SCHEMA_VERSION,
    SHELL_UNSAFE_RE,
    VALID_BUILD_KINDS,
    ValidationResult,
    find_manifest,
    load_contracts,
    load_lock,
    load_manifest,
    print_result,
    scan_secrets,
    validate_elf_riscv,
    validate_manifest,
)
from devkit.reports import (  # noqa: F401,E402
    STAGES,
    STATUSES,
    new_report,
    report_ok,
    set_stage,
    write_report,
)
from devkit.schema import validate_schema  # noqa: F401,E402
from devkit.transport import RemoteResult, SSH, SSHError, remote_sudo_command  # noqa: F401,E402
from devkit.util import (  # noqa: F401,E402
    SKILL_ROOT,
    human_size,
    load_jsonl,
    now_iso,
    read_json,
    redact,
    semver_tuple,
    sha256_bytes,
    sha256_file,
    write_json,
)


def fail(message: str, code: int = 1) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)