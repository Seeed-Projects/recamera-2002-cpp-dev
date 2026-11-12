#!/bin/sh
# Cross-compile entry point. build_demo.py runs this from the demo root with
# PATH pointing at the RISC-V musl toolchain and SG200X_SDK_PATH exported.
set -eu

CC=${CC:-riscv64-unknown-linux-musl-gcc}
mkdir -p dist
"$CC" -O2 -Wall -Wextra src/main.c -o "dist/{{BINARY}}"