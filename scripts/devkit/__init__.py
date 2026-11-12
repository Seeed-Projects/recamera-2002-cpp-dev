"""reCamera 2002 developer toolkit.

The package is deliberately standard-library only. Device access happens only
through ``transport``; everything else is safe to run offline.

Modules
-------
util        small helpers (JSON, hashing, redaction)
contracts   demo.json validation and contract loading
transport   ssh/scp wrapper with password-free argv
artifacts   toolchain discovery and deterministic packaging
reports     stage-separated machine-readable reports
schema      minimal JSON Schema validator (no third-party dependency)
"""

from .artifacts import deterministic_tar, find_toolchain, compiler_path  # noqa: F401
from .contracts import (  # noqa: F401
    SCHEMA_VERSION,
    ValidationResult,
    find_manifest,
    load_contracts,
    load_manifest,
    scan_secrets,
    validate_elf_riscv,
    validate_manifest,
)
from .reports import (  # noqa: F401
    STAGES,
    new_report,
    report_ok,
    set_stage,
    write_report,
)
from .schema import validate_schema  # noqa: F401
from .transport import RemoteResult, SSH, SSHError, remote_sudo_command  # noqa: F401
from .util import (  # noqa: F401
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