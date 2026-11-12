# Knowledge base specification

The knowledge base reuses experience across demos; it must be script-validatable and directly usable by the next person.

## Files

```text
knowhubs/reCamera_KnowHub/
├── README.md              # directory guide and how to extend
├── overview.md            # device access/services/build & deploy/recording/wiki mapping
├── demo-records.md        # capability catalog + record format (no personal records bundled)
└── model-conversion.md    # CVI/TPU-MLIR conversion methodology (historical results marked not_run)
```

For a new device, create `knowhubs/<device>_KnowHub/` with the same file split and register it under Knowledge base in `SKILL.md`.

## Record format for run demos

Write location: the default user data directory `~/.local/share/recamera-2002-cpp-dev/demo-records.md`
(changeable via `XDG_DATA_HOME` or `RECAMERA_DATA_DIR`); on the first `record_demo.py` run it initializes
from the skill's template at `knowhubs/reCamera_KnowHub/demo-records.md`; `--records`
can explicitly specify the file. **Do not write personal records back into the installed skill directory** — upgrading the skill will overwrite or conflict with them.
The example shipped with the skill is `examples/community-records.md`, marked as unofficial.

```markdown
### <demo_name> (<YYYY-MM-DD>)
- Status: ✅ success / ❌ failed
- Capability: <one line>
- Model files: <files or "none">
- Dependencies: <deps or "none">
- Build issues: <issue and fix, or "none">
- Verification: <actual observed result>
- Lessons: <reusable conclusion or "none">
- Wiki issues: <optional>
```

Rules:

- Status must begin with `✅` or `❌`; failures must be recorded too, not just successes.
- "Verification" lists only checks actually performed; for items not executed write "not executed".
- Never write passwords, tokens, private keys, or absolute host paths.
- Do not record the same `name + date` twice (`record_demo.py` rejects it).
- `Wiki issues` contains only verifiable facts (missing command, missing path, ambiguous parameter); avoid evaluative wording.
  It is material for filing upstream issues, not a grade on the docs.
- Working command templates may go in code blocks under the entry, but credentials must use environment variables.

## Capability catalog

The `## Capability catalog` section of `demo-records.md` lists wiki pages by category:

```markdown
`reCamera_Basic/application/xxx.md`
- Capability: ...
- Verification: ...
```

Use it before starting work: search by demo name/model/task to judge whether a similar capability exists, what hardware is needed, and how to verify. Already-run demos are not duplicated.

## Wiki page mapping and drift

The mapping table in `overview.md` is the single source for wiki paths; `configs/wiki-snapshot.lock.json` locks the snapshot date and page list.

```bash
python3 scripts/check_knowledge.py --update-lock   # refresh the lock after the overview table changes
python3 scripts/check_knowledge.py --online        # whether the upstream branch has new commits
python3 scripts/check_knowledge.py --online-full   # check page by page
```

Upstream repository: `Seeed-Studio/wiki-documents`, branch `docusaurus-version`, reCamera directory `sites/en/docs/Edge/reCamera/`. Upstream restructuring changes paths; treat the mapping-table snapshot and the online check as authoritative.

## Maintenance triggers

The following changes must update the knowledge base at the same time:

- Device OS/API version change → update `os.minimum` / `tested` in `configs/device-contract.json` and state the measured evidence.
- New demo conclusion → write back via `record_demo.py`.
- New model-conversion method/pitfall → `model-conversion.md`.
- Wiki pages added/removed → `overview.md` + `--update-lock`.
- New device/new hardware limitation → a new knowhub or the hardware notes in `overview.md`.

## Validation

```bash
python3 scripts/check_knowledge.py
python3 -m pytest tests -q
```

`check_knowledge.py` checks: required files, config JSON, record fields and statuses, Markdown links, script syntax, secret patterns, wiki lock consistency.
