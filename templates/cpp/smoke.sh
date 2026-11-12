#!/bin/sh
# Host-side smoke test: compiles the same logic with the host compiler and
# runs --self-test. Never loads a model or touches the device.
set -eu

CC=${CC:-cc}
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

"$CC" -O1 -Wall -Wextra -o "$tmp/demo" src/main.c
"$tmp/demo" --self-test