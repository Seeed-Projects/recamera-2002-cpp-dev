#!/usr/bin/env python3
"""Validate the reCamera 2002 C++ Dev skill's knowledge base and contracts.

Checks required files, demo-records entry schema, markdown links, config JSON,
Python syntax and the pinned wiki page map. --update-lock refreshes the wiki
snapshot lock; --online checks the upstream wiki branch for new commits.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from demo_lib import SKILL_ROOT, now_iso, read_json, validate_schema, write_json  # noqa: E402

REQUIRED = [
    "SKILL.md",
    "README.md",
    "NOTICE",
    "LICENSE",
    "agents/openai.yaml",
    "workflows/demo-output.md",
    "workflows/live-demo.md",
    "knowhubs/reCamera_KnowHub/README.md",
    "knowhubs/reCamera_KnowHub/overview.md",
    "knowhubs/reCamera_KnowHub/demo-records.md",
    "knowhubs/reCamera_KnowHub/model-conversion.md",
    "configs/device-contract.json",
    "configs/evaluation-contract.json",
    "configs/wiki-snapshot.lock.json",
    "configs/toolchain.lock.json",
    "configs/model-assets.lock.json",
    "schemas/demo.schema.json",
    "schemas/recipe.schema.json",
    "schemas/report.schema.json",
    "references/demo-contract.md",
    "references/capability-routing.md",
    "references/doc-router.md",
    "references/evaluation.md",
    "references/evidence.md",
    "references/scripts.md",
    "references/knowledge-base.md",
    "references/tools/scaffold.md",
    "references/tools/sdk.md",
    "references/tools/fetch-models.md",
    "references/tools/convert.md",
    "references/tools/build.md",
    "references/tools/smoke.md",
    "references/tools/package.md",
    "references/tools/deploy.md",
    "references/tools/serve.md",
    "references/tools/launch.md",
    "references/tools/device-pipeline.md",
    "scripts/demo_lib.py",
    "scripts/devkit/__init__.py",
    "scripts/devkit/util.py",
    "scripts/devkit/contracts.py",
    "scripts/devkit/transport.py",
    "scripts/devkit/artifacts.py",
    "scripts/devkit/reports.py",
    "scripts/devkit/schema.py",
    "scripts/devkit/live.py",
    "scripts/new_demo.py",
    "scripts/fetch_models.py",
    "scripts/convert_model.py",
    "scripts/probe_device.py",
    "scripts/validate_demo.py",
    "scripts/build_demo.py",
    "scripts/smoke_demo.py",
    "scripts/package_demo.py",
    "scripts/deploy_demo.py",
    "scripts/launch_demo.py",
    "scripts/serve_demo.py",
    "scripts/capture_evidence.py",
    "scripts/evaluate_demo.py",
    "scripts/record_demo.py",
    "scripts/diagnose_demo.py",
    "scripts/check_knowledge.py",
    "templates/cpp/demo.json",
    "templates/cpp/src/main.c",
    "templates/cpp/smoke.sh",
    "templates/cmake/demo.json",
    "templates/cmake/src/CMakeLists.txt",
    "templates/cmake/src/main.c",
    "templates/cmake/smoke.sh",
    "templates/script/demo.json",
    "templates/script/build.sh",
    "templates/script/smoke.sh",
    "templates/script/src/main.c",
    "templates/common/README.md",
    "templates/common/gitignore",
    "templates/common/eval/input/manifest.json",
    "templates/live/src/main.cpp",
    "templates/live/src/live_udp.h",
    "templates/live/src/live_udp.cpp",
    "templates/live/src/live_params.h",
    "templates/live/CMakeLists.txt",
    "templates/live/README.md",
    "templates/live/smoke.sh",
    "templates/live-ui/index.html",
    "templates/live-ui/app.js",
    "templates/live-ui/style.css",
    "templates/live-ui/vendor/jmuxer.min.js",
    "templates/live-ui/vendor/LICENSE.jmuxer",
    "examples/hello_cpp/demo.json",
    "examples/hello_cpp/src/main.c",
    "examples/community-records.md",
]
ENTRY_FIELDS = ("Status", "Capability", "Model files", "Dependencies", "Build issues",
                "Verification", "Lessons")
# Legacy Chinese labels/heading stay readable so older demo-records.md files
# keep validating; glyphs are encoded as \uXXXX escapes to keep this source ASCII.
LEGACY_ENTRY_FIELDS = ("\u72b6\u6001", "\u80fd\u529b", "\u6a21\u578b\u6587\u4ef6", "\u7279\u6b8a\u4f9d\u8d56", "\u7f16\u8bd1\u95ee\u9898",
                       "\u9a8c\u8bc1\u7ed3\u679c", "\u5173\u952e\u7ecf\u9a8c")
FULLWIDTH_COLON = "\uff1a"
RUN_SECTION_RE = re.compile(r"^## (?:Demos run so far|\u5df2\u8dd1\u8fc7\u7684 demo)\s*$", re.M)
WIKI_REPO = "Seeed-Studio/wiki-documents"
WIKI_BRANCH = "docusaurus-version"
WIKI_PREFIX = "sites/en/docs/Edge/reCamera/"
LOCK = "configs/wiki-snapshot.lock.json"


class Checker:
    def __init__(self, root: Path):
        self.root = root
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.info: list[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def check_required(self) -> None:
        for rel in REQUIRED:
            if not (self.root / rel).is_file():
                self.error(f"required file missing: {rel}")
        for rel in ("scripts", "tests", "references"):
            if not (self.root / rel).is_dir():
                self.error(f"required directory missing: {rel}")

    def check_agent_contract(self) -> None:
        """Validate the Claude/Codex shared entry contract.

        Both agents read SKILL.md with ``name`` + ``description`` frontmatter;
        Codex additionally uses agents/openai.yaml for UI metadata.
        """
        skill = self.root / "SKILL.md"
        if not skill.is_file():
            return
        text = skill.read_text(encoding="utf-8")
        if not text.startswith("---"):
            self.error("SKILL.md: missing YAML frontmatter (Claude and Codex both require it)")
            return
        end = text.find("\n---", 3)
        if end < 0:
            self.error("SKILL.md: frontmatter is not closed")
            return
        fields: dict[str, str] = {}
        for line in text[3:end].splitlines():
            match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
            if match:
                fields[match.group(1)] = match.group(2).strip().strip("\"'")
        for key in ("name", "description"):
            if not fields.get(key):
                self.error(f"SKILL.md: frontmatter missing {key}")
        name = fields.get("name", "")
        if name and not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
            self.error(f"SKILL.md: name '{name}' must be lowercase letters/digits/hyphens")
        if name and self.root.name != name:
            self.error(f"SKILL.md: name '{name}' must match directory '{self.root.name}'")

        meta = self.root / "agents/openai.yaml"
        if not meta.is_file():
            return
        meta_text = meta.read_text(encoding="utf-8")
        for key in ("display_name", "short_description", "default_prompt"):
            if not re.search(rf"^\s+{key}:\s*\S", meta_text, re.M):
                self.error(f"agents/openai.yaml: missing interface.{key}")
        if not re.search(r"^policy:\s*$", meta_text, re.M):
            self.error("agents/openai.yaml: missing policy block")
        elif not re.search(r"^\s+allow_implicit_invocation:\s*(?:true|false)\s*$", meta_text, re.M):
            self.error("agents/openai.yaml: policy.allow_implicit_invocation must be true|false")

    def check_configs(self) -> None:
        try:
            device = read_json(self.root / "configs/device-contract.json")
            eval_contract = read_json(self.root / "configs/evaluation-contract.json")
        except (OSError, json.JSONDecodeError) as exc:
            self.error(f"config JSON: {exc}")
            return
        if device.get("schema_version") != 1 or eval_contract.get("schema_version") != 1:
            self.error("config schema_version must be 1")
        if "reCamera 2002" not in device.get("devices", {}):
            self.error("device-contract has no reCamera 2002 profile")
        for task in ("detection", "classification", "ocr", "segmentation", "pose", "custom"):
            if task not in eval_contract.get("tasks", {}):
                self.error(f"evaluation-contract missing task: {task}")

    def check_records(self) -> None:
        path = self.root / "knowhubs/reCamera_KnowHub/demo-records.md"
        if not path.is_file():
            return
        text = path.read_text(encoding="utf-8")
        if not RUN_SECTION_RE.search(text):
            self.error("demo-records.md: missing run-records section '## Demos run so far'")
            return
        heading = re.compile(r"^### (.+) \((\d{4}-\d{2}-\d{2})\)$", re.M)
        entries = list(heading.finditer(text))
        self.info.append(f"demo-records: {len(entries)} run entr(ies)")
        for match in entries:
            start = match.end()
            next_heading = re.search(r"^### |^## ", text[start:], re.M)
            block = text[start:start + (next_heading.start() if next_heading else len(text))]
            for field, legacy in zip(ENTRY_FIELDS, LEGACY_ENTRY_FIELDS):
                if f"- {field}:" not in block and f"- {legacy}{FULLWIDTH_COLON}" not in block:
                    self.error(f"demo-records.md: {match.group(1)} ({match.group(2)}) missing {field}")
            status = re.search(r"- Status: (.+)", block) or re.search(
                rf"- {LEGACY_ENTRY_FIELDS[0]}{FULLWIDTH_COLON}(.+)", block)
            if status and not status.group(1).strip().startswith(("✅", "❌")):
                self.error(f"demo-records.md: {match.group(1)} status must start with ✅ or ❌")

    def check_links(self) -> None:
        link_re = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
        for path in self.root.rglob("*.md"):
            if any(part in {".git", "node_modules"} for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8")
            for target in link_re.findall(text):
                target = target.strip().split("#", 1)[0]
                if not target or target.startswith(("http://", "https://", "mailto:", "/")):
                    continue
                resolved = (path.parent / target).resolve()
                if not resolved.exists():
                    # The workflow historically links from the skill root.
                    resolved = (self.root / target).resolve()
                try:
                    resolved.relative_to(self.root.resolve())
                except ValueError:
                    self.warn(f"{path.relative_to(self.root)}: link escapes skill root: {target}")
                    continue
                if not resolved.exists():
                    self.error(f"{path.relative_to(self.root)}: broken link {target}")

    def check_schemas(self) -> None:
        for name in ("demo.schema.json", "recipe.schema.json", "report.schema.json"):
            path = self.root / "schemas" / name
            try:
                read_json(path)
            except (OSError, json.JSONDecodeError) as exc:
                self.error(f"schema JSON: {name}: {exc}")
        try:
            demo_schema = read_json(self.root / "schemas/demo.schema.json")
            example = read_json(self.root / "examples/hello_cpp/demo.json")
        except (OSError, json.JSONDecodeError):
            return
        for error in validate_schema(example, demo_schema):
            self.error(f"examples/hello_cpp/demo.json: schema: {error}")

        try:
            report_schema = read_json(self.root / "schemas/report.schema.json")
        except (OSError, json.JSONDecodeError):
            return
        sample = {
            "schema_version": 1,
            "kind": "knowledge-check",
            "generated_at": now_iso(),
            "demo": None,
            "stages": {"static": {"status": "passed"}},
        }
        for error in validate_schema(sample, report_schema):
            self.error(f"schemas/report.schema.json: sample report: {error}")

    def check_locks(self) -> None:
        try:
            toolchain = read_json(self.root / "configs/toolchain.lock.json")
        except (OSError, json.JSONDecodeError) as exc:
            self.error(f"toolchain lock: {exc}")
            toolchain = {}
        if toolchain and not toolchain.get("minimum_gcc"):
            self.error("toolchain.lock.json: minimum_gcc is required")
        if toolchain and not isinstance(toolchain.get("search_order"), list):
            self.error("toolchain.lock.json: search_order must be a list")

        try:
            assets = read_json(self.root / "configs/model-assets.lock.json")
        except (OSError, json.JSONDecodeError) as exc:
            self.error(f"model asset lock: {exc}")
            return
        if not assets.get("base_url"):
            self.error("model-assets.lock.json: base_url is required")
        entries = assets.get("assets", {})
        if not entries:
            self.error("model-assets.lock.json: assets is empty")
        for name, asset in entries.items():
            if not asset.get("file"):
                self.error(f"model-assets.lock.json: {name} has no file")
            if not re.fullmatch(r"[0-9a-f]{64}", str(asset.get("sha256", ""))):
                self.error(f"model-assets.lock.json: {name} sha256 must be 64 lowercase hex")
            if not isinstance(asset.get("size"), int) or asset.get("size", 0) <= 0:
                self.error(f"model-assets.lock.json: {name} size must be positive")

    def check_syntax(self) -> None:
        import py_compile
        for path in sorted((self.root / "scripts").rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            try:
                py_compile.compile(str(path), doraise=True, cfile=str(path) + ".pyc")
            except py_compile.PyCompileError as exc:
                self.error(f"python syntax: {path.name}: {exc.msg}")
            finally:
                Path(str(path) + ".pyc").unlink(missing_ok=True)

    def check_secrets(self) -> None:
        patterns = [
            (re.compile(r"sshpass\s+-p\s+\S+"), "literal sshpass password"),
            (re.compile(r"BEGIN (?:RSA|OPENSSH|EC|DSA) PRIVATE KEY"), "private key"),
            (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "GitHub token"),
            (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "API key"),
            (re.compile(
                r"(?i)\b(?:password|passwd|pwd)\s*[:=]\s*['\"][^'\"]{3,}['\"]"),
             "literal password assignment"),
            (re.compile(
                r"(?:\u5bc6\u7801|\u53e3\u4ee4)\s*(?:[:\uff1a=]\s*)?`(?!export\b|RECAMERA_PASSWORD\b|<|\$)[^`\n]{3,}`"),
             "literal password in backticks"),
        ]
        doc_only_patterns = [
            (re.compile(
                r"(?im)^\s*(?:password|passwd|pwd)\s*[:=]\s*"
                r"(?!<|\$|export\b|RECAMERA_PASSWORD\b)['\"]?[^\s'\"#]{3,}"),
             "literal password assignment"),
        ]
        code_suffixes = {".py", ".sh", ".c", ".cpp", ".h"}
        host_paths = [
            (re.compile(
                r"/(?:home|Users)/(?!recamera(?:/|$)|root(?:/|$))[A-Za-z0-9._-]+/"),
             "personal absolute path"),
            (re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+"), "personal absolute path"),
        ]
        suffixes = {".md", ".py", ".json", ".yaml", ".yml", ".sh", ".c", ".cpp", ".h",
                    ".html", ".js", ".jsonl", ".txt"}
        for path in self.root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in suffixes:
                continue
            if "tests" in path.parts or "__pycache__" in path.parts or "vendor" in path.parts:
                continue
            if path.stat().st_size > 2_000_000:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            to_check = patterns + host_paths
            if path.suffix.lower() not in code_suffixes:
                to_check += doc_only_patterns
            for pattern, label in to_check:
                if pattern.search(text):
                    self.error(f"{path.relative_to(self.root)}: {label}")

    def wiki_pages(self) -> list[dict]:
        overview = self.root / "knowhubs/reCamera_KnowHub/overview.md"
        text = overview.read_text(encoding="utf-8")
        pages: list[dict] = []
        seen: set[str] = set()
        for line in text.splitlines():
            if not line.startswith("|"):
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) < 2:
                continue
            for token in re.findall(r"`([^`]+\.md)`", cells[1]):
                if token.startswith("reCamera_Pro/"):
                    continue
                if token not in seen:
                    seen.add(token)
                    pages.append({"path": token, "area": cells[0]})
        return pages

    def check_lock(self, update: bool) -> list[dict]:
        pages = self.wiki_pages()
        if not pages:
            self.error("overview.md: wiki mapping table has no pages")
        if update:
            overview = (self.root / "knowhubs/reCamera_KnowHub/overview.md").read_text(encoding="utf-8")
            match = re.search(r"(?:\u8def\u5f84\u5feb\u7167\uff1a|Snapshot date: )(\d{4}-\d{2}-\d{2})", overview)
            lock = {
                "schema_version": 1,
                "repository": WIKI_REPO,
                "branch": WIKI_BRANCH,
                "prefix": WIKI_PREFIX,
                "snapshot_date": match.group(1) if match else now_iso()[:10],
                "generated_at": now_iso(),
                "pages": pages,
            }
            write_json(self.root / LOCK, lock)
            self.info.append(f"wiki lock updated: {len(pages)} page(s)")
            return pages
        try:
            lock = read_json(self.root / LOCK)
        except (OSError, json.JSONDecodeError):
            self.error(f"wiki lock missing: {LOCK} (run --update-lock)")
            return pages
        locked = {p["path"] for p in lock.get("pages", [])}
        current = {p["path"] for p in pages}
        for missing in sorted(current - locked):
            self.error(f"wiki page not in lock: {missing}")
        for stale in sorted(locked - current):
            self.warn(f"wiki page in lock but not in overview: {stale}")
        return pages

    def check_online(self, pages: list[dict], full: bool) -> None:
        def get(url: str):
            request = urllib.request.Request(url, headers={"User-Agent": "recamera-demo-skill"})
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.loads(response.read().decode("utf-8"))

        try:
            head = get(f"https://api.github.com/repos/{WIKI_REPO}/commits?sha={WIKI_BRANCH}&per_page=1")
            if head:
                date = head[0]["commit"]["committer"]["date"]
                self.info.append(f"wiki branch head: {head[0]['sha'][:12]} {date}")
                try:
                    snapshot = read_json(self.root / LOCK).get("snapshot_date", "")
                except (OSError, json.JSONDecodeError):
                    snapshot = ""
                if snapshot and date[:10] > snapshot:
                    self.warn(f"wiki branch has commits after snapshot {snapshot}; run --online-full or review changed pages")
        except (urllib.error.URLError, json.JSONDecodeError, KeyError) as exc:
            self.warn(f"online wiki check skipped: {exc}")
            return
        if not full:
            self.info.append("per-page check skipped; pass --online-full to check every page")
            return
        snapshot = "2026-09-18"
        try:
            snapshot = read_json(self.root / LOCK).get("snapshot_date", snapshot)
        except (OSError, json.JSONDecodeError):
            pass
        for page in pages:
            url = (f"https://api.github.com/repos/{WIKI_REPO}/commits?sha={WIKI_BRANCH}"
                   f"&per_page=1&path={WIKI_PREFIX}{page['path']}")
            try:
                commits = get(url)
                if commits and commits[0]["commit"]["committer"]["date"][:10] > snapshot:
                    self.warn(f"wiki updated after snapshot: {page['path']}")
            except (urllib.error.URLError, json.JSONDecodeError, KeyError) as exc:
                self.warn(f"wiki page check failed for {page['path']}: {exc}")
                break


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill-root", type=Path, default=SKILL_ROOT)
    parser.add_argument("--update-lock", action="store_true")
    parser.add_argument("--online", action="store_true")
    parser.add_argument("--online-full", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    checker = Checker(args.skill_root.resolve())
    checker.check_required()
    checker.check_agent_contract()
    checker.check_configs()
    checker.check_schemas()
    checker.check_locks()
    checker.check_records()
    checker.check_links()
    checker.check_syntax()
    checker.check_secrets()
    pages = checker.check_lock(args.update_lock)
    if args.online or args.online_full:
        checker.check_online(pages, args.online_full)

    report = {
        "schema_version": 1,
        "kind": "knowledge-check",
        "generated_at": now_iso(),
        "skill_root": str(checker.root),
        "pages": len(pages),
        "errors": checker.errors,
        "warnings": checker.warnings,
        "info": checker.info,
        "ok": not checker.errors,
    }
    if args.report:
        write_json(args.report, report)
    for item in checker.info:
        print(f"INFO  {item}")
    for item in checker.warnings:
        print(f"WARN  {item}")
    for item in checker.errors:
        print(f"ERROR {item}")
    print(f"knowledge check: {'PASS' if report['ok'] else 'FAIL'}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())