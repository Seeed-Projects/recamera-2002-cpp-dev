# Packaging: `package_demo.py`

Produces a distributable deterministic archive.

```bash
python3 scripts/package_demo.py --demo-dir <dir>
python3 scripts/package_demo.py --demo-dir <dir> --mode publish
python3 scripts/package_demo.py --demo-dir <dir> --out /tmp/dist
```

## Prerequisites

- The `deliver` tier of `validate_demo.py` (the `publish` tier is stricter) must pass.
- `cpp`/`cmake`/`prebuilt` must go through `build_demo.py` first; the archive includes `dist/<binary>`.

## Archive contents

```text
<name>-<version>/
├── demo.json
├── README.md
├── src/...
├── models/...              # if present
├── eval/input/manifest.json
├── build-report.json       # if present
├── dist/<binary>
└── files.sha256
```

Excluded: other `dist/` contents, `build/`, `eval/baseline`, `eval/recamera`, `.git`, caches.

## Determinism

- Entries sorted by path.
- File timestamps fixed to 0, uid/gid 0, uname/gname empty.
- gzip mtime 0.
- Repackaging the same inputs yields the same `.tar.gz` SHA-256 (measured).

`build-report.json` contains `generated_at`, so repackaging after a rebuild changes the hash; this is expected.

## Report

`dist/package-report.json` uses the six-stage structure: `static` and `offline` are `passed`,
the three device stages are `not_run`. The archive path, SHA-256, size, and per-file manifest all go into the report.

## Extra requirements for the publish tier

- `README.md` must exist.
- When a `model` is declared it must carry `model.conversion` provenance.
- Adding `LICENSE` is recommended (missing is only a warning).
- The version should not still be `1.0.0`/`0.0.0` (warning).
