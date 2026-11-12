# Changelog

All notable changes to this skill are documented here. This project follows
[Semantic Versioning](https://semver.org/).

## [0.1.1] - 2026-11-12

### Changed

- Relicensed the skill from MIT to the Apache License, Version 2.0. The
  bundled jmuxer player remains MIT; see `NOTICE`.
- Updated license references in `README.md`, `SKILL.md`, `.skillrc`, `NOTICE`,
  and the knowledge hub.

## [0.1.0] - 2026-11-05

### Added

- Public release metadata: `LICENSE` (MIT), `CHANGELOG.md`, `.skillrc`,
  version and maintainer fields in `README.md` and `SKILL.md`.
- `compatibility` and `license` fields in the `SKILL.md` frontmatter.
- Secret-scanning coverage for Chinese password phrases and generic home
  paths; regression tests for both.
- Explicit `--allow-service-stop` authorization on `launch_demo.py`.
- `Referrer-Policy`, `X-Content-Type-Options` and `X-Frame-Options` on the
  live viewer's HTTP responses.

### Changed

- Renamed the skill to `recamera-2002-cpp-dev` (display name: reCamera 2002
  C++ Dev). The directory, frontmatter, README, and `agents/openai.yaml` are
  aligned.
- The live viewer now binds to `127.0.0.1` and disables browser actions by
  default. LAN sharing requires explicit `--bind 0.0.0.0`; browser
  install/start/stop requires `--allow-actions`.
- `launch_demo.py` no longer passes `--allow-service-stop` unconditionally.
- Documentation now describes the real build scope: `build_demo.py` covers
  SDK-free host smoke tests; SDK-linked camera/NPU demos are built in the
  `sscma-example-sg200x` tree.
- Replaced internal device addresses, personal absolute paths, and internal
  network notes with generic placeholders (`<device-ip>`, `<host-ip>`,
  `$SG200X_SDK_PATH`).
- Rewrote `README.md` around the product workflow (capabilities, contract,
  evidence levels, security model), with safe defaults first and an explicit
  split between the toolkit deploy root `/userdata/demos/` and the wiki
  baseline path `/home/recamera/`.
- Removed internal demo names and run dates from `references/`; example
  timestamps are now placeholders.
- Device contract now marks OS 0.2.4 as the supported minimum; older versions
  are reported as unverified.

### Fixed

- `deploy_demo.py start` now restores Node-RED/Supervisor services if the
  demo fails to start after services were stopped.
- `HOST_PATH_RE` no longer hardcodes a personal home directory; it flags
  generic user home paths while allowing device paths such as
  `/home/recamera`.
- Removed personal and internal deployment sections from `SKILL.md` and
  `README.md`, including the plaintext device password.

### Security

- Removed the plaintext device credential and the “default device” behavior.
  The agent now asks for a device address when none is provided and reads the
  password only from `RECAMERA_PASSWORD`.
- The release checklist now treats any credential in documentation as a
  release blocker, including Chinese-language password phrases.