# Cross-compilation: `build_demo.py`

## Host platform

The standard toolchain is the RISC-V musl GCC for **Linux x86_64** (path
`host-tools/gcc/riscv64-linux-musl-x86_64/bin`). macOS/Windows do not have this
toolchain natively; use a Linux host, a Linux container (Docker/Dev Container), or WSL2.
`build_demo.py` only handles SDK-free `cpp`/`cmake`/`script` builds; camera/NPU demos
that link `sscma-micro` should be built inside the `sscma-example-sg200x` SDK tree.

```bash
python3 scripts/build_demo.py --demo-dir <dir>
python3 scripts/build_demo.py --demo-dir <dir> --check-only
python3 scripts/build_demo.py --demo-dir <dir> --strict-toolchain
```

## Supported build.kind values

| kind | Behavior |
|---|---|
| `cpp` | Compile `build.sources` directly with the RISC-V musl gcc/g++ |
| `cmake` | Set `CC`/`CXX`/`PATH`, then run `cmake -S src -B build` and `cmake --build` |
| `prebuilt` | Copy `build.artifact` to `dist/` and verify `build.sha256` |
| `script` | Run `build.script`; it must produce `dist/<binary>` |

The output is always `dist/<binary>` + `dist/build-report.json`.

## Toolchain resolution order

1. `--toolchain-bin`
2. `TOOLCHAIN_BIN`
3. `$SG200X_SDK_PATH/host-tools/gcc/riscv64-linux-musl-x86_64/bin`
4. `~/host-tools/gcc/riscv64-linux-musl-x86_64/bin`
5. `PATH`

## Toolchain lock

`configs/toolchain.lock.json` records the verified minimum GCC version (currently 10.2.0, Xuantie-900 V2.6.1)
and the search order. `build_demo.py` compares against the actual version:

- Default: prints `ERROR` when below the minimum, but continues the build anyway (convenient for older environments).
- `--strict-toolchain`: returns 2 immediately; recommended for CI/releases.

`build-report.json` records a `toolchain_lock` field: the resolved path, the first line of the actual `--version`
output, and whether the lock was matched.

## Artifact validation

- The file must be ELF64, little-endian, with `e_machine=243` (RISC-V), otherwise it fails.
- The report records the SHA-256 of every source file and of the artifact, for reproducibility.

## Common failures

| Symptom | Cause |
|---|---|
| `RISC-V musl toolchain not found` | Toolchain not installed, or `TOOLCHAIN_BIN` not set |
| `not an ELF file` | `prebuilt.artifact` is a script or an x86 binary |
| `e_machine=62` | Compiled with the host gcc |
| cmake cannot find the compiler | Toolchain hard-coded in `cmake_args`; use `CC`/`CXX` instead |
