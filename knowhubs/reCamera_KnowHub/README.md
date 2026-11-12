# reCamera Knowledge Hub

This is the knowledge base for reCamera devices. It covers technical knowledge, best practices and known issues related to reCamera.

## Directory structure

- `overview.md` - Overview and device operations (Wiki scope and source/license: see `NOTICE` at the skill root; original page index, device access/SSH/scp/UDP receiver IP/tunnel screenshots, OS and services/stopping services, C++ build and deployment baseline, receiver-side recording, model list, evidence expectations)
- `demo-records.md` - Capability catalog and record format (what each capability does and how to verify it); personal records measured on device are written to the user data directory by default and never back into the installed skill
- `model-conversion.md` - Custom model conversion and NPU demo methodology (historical records are marked not_run; re-verify before converting)

Companion files at the skill root:

- `references/` - demo contracts, evaluation, evidence, scripts, knowledge-hub conventions, and the task-routing `doc-router.md`
- `references/tools/` - per-tool docs for scaffold / fetch-models / convert / build / smoke / package / deploy
- `configs/` - device contract, evaluation contract, wiki snapshot lock, toolchain lock, official model asset lock
- `schemas/` - JSON Schemas for `demo.json`, conversion recipes, and stage reports
- `templates/` - cpp / cmake / script / common templates used by `new_demo.py`
- `scripts/` - scaffolding, model fetch/conversion, validation, build, smoke, packaging, deployment, evidence, scoring, recording, and diagnostic tools

## Usage

Reference this knowledge hub by relative path within the skill's workflow:

```
knowhubs/reCamera_KnowHub/demo-records.md
```

Entry points developers use most often:

```bash
python3 scripts/new_demo.py --name <demo> --task <task> --out ~/reCamera_demo
python3 scripts/fetch_models.py --list
python3 scripts/convert_model.py check-env
python3 scripts/build_demo.py --demo-dir ~/reCamera_demo/<demo>
python3 scripts/smoke_demo.py --demo-dir ~/reCamera_demo/<demo>
python3 scripts/package_demo.py --demo-dir ~/reCamera_demo/<demo>
```

Read-only device inventory at the start of the workflow:

```bash
python3 scripts/probe_device.py --host <device-ip>
```

## Extending

Prefer merging new knowledge into the corresponding section of the three files above; create a new Markdown file only for a genuinely new topic, and update this README. When adding a new device, create `knowhubs/<device>_KnowHub/`, register it in `SKILL.md`, and add device facts measured on device to `configs/device-contract.json`.
