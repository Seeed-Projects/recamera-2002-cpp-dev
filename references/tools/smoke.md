# Host smoke test: `smoke_demo.py`

Verifies the demo's **pure logic** without a device. It does not load `.cvimodel`, does not open a camera,
and does not connect to a reCamera.

```bash
python3 scripts/smoke_demo.py --demo-dir <dir>
python3 scripts/smoke_demo.py --demo-dir <dir> --fixture eval/input/images/0001.jpg
python3 scripts/smoke_demo.py --archive dist/<name>-<version>.tar.gz
```

`--archive` unpacks safely (rejecting absolute paths, `..`, symlinks) and runs inside the archive,
to verify the **final artifact** rather than the working directory. The report defaults to `smoke-report.json` in the current directory.

## demo.json declaration

```json
"smoke": {
  "kind": "host-logic",
  "command": ["sh", "smoke.sh"],
  "fixtures": ["eval/input/images"],
  "timeout_s": 15,
  "expect_stdout": ["self-test ok"]
}
```

| Field | Description |
|---|---|
| `kind` | `host-logic` / `mock` / `none` |
| `command` | argv list, not passed through a shell; metacharacters are not allowed |
| `fixtures` | Paths relative to the demo root, exposed at runtime via `DEMO_FIXTURES` (OS path separator) |
| `timeout_s` | 1..3600, default 15 |
| `expect_stdout` | Markers that stdout+stderr must contain |

## Behavior

- The command runs in the demo root with `DEMO_DIR` in the environment.
- Exit code `0` and every marker seen, or it is not a PASS.
- No `smoke` section or `kind=none`: report `not_run`, **exit code 2**, not judged a failure.
- Command times out or is missing: FAIL, exit code 1.

## What it can prove

- Can prove: pure C/Python logic, parsing, state machines, mock-driven behavior.
- Cannot prove: visual results, NPU inference, camera chain, device compatibility.

C++ vision demos have no host runtime, so `smoke` can only cover the device-independent parts. Never write
`not_run` up as "passed".
