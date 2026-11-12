#!/bin/sh
# Host-side smoke test for the live UDP skeleton. Compiles the transport and
# runs both the plain self-test and the loopback transport/control test.
# No camera, model or device is touched.
set -eu

CXX=${CXX:-c++}
ATOMIC=""
case "$(uname -s 2>/dev/null || echo unknown)" in
  Linux) ATOMIC="-latomic" ;;
esac
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

"$CXX" -std=c++17 -O1 -Wall -Wextra -pthread -Isrc \
  -o "$tmp/demo" src/main.cpp src/live_udp.cpp $ATOMIC
"$tmp/demo" --self-test
"$tmp/demo" --live-self-test