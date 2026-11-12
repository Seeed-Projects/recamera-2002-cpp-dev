#!/bin/sh
# Host-side smoke test for the CMake template.
set -eu

CC=${CC:-cc}
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

"$CC" -O1 -Wall -Wextra -o "$tmp/demo" src/main.c
"$tmp/demo" --self-test