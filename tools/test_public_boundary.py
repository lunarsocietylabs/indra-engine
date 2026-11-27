#!/usr/bin/env python3
"""Non-sensitive regression fixtures for the public disclosure guard.

Runs on the public standard GitHub Runner. Never contains real credentials or
private source code. Test samples are synthetic and generated in a temp dir.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile

from check_public_boundary import check


def fixture(files: dict[str, str], expect: int) -> None:
    with tempfile.TemporaryDirectory(prefix="indra-public-boundary-") as temp:
        root = Path(temp)
        for path, content in files.items():
            out = root / path
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(content, encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            status = check(root, proposal=True)
        assert status == expect, f"expected status {expect}, got {status}; {buf.getvalue()}"


def main() -> None:
    fixture({"README.md": "# original open source\n",
             "indra/v4_batch.py": "# public batch wrapper only\n"}, 0)
    fixture({"engine/v4/indra/some.py": "# fake proprietary code\n"}, 1)
    fixture({"indra/v4_locate.py": "# renamed public source\n"}, 1)
    fixture({"src/renamed.py": "v4_locate.py" + " — 注经引擎 v4"}, 1)
    fixture({"src/another.py": "def thresholds_all" + "(true_pairs, decoy_pairs): pass"}, 1)
    fixture({".env.production": "FAKE=value\n"}, 1)
    fixture({"src/secret.py": 'OPENAI_API_KEY="' + "A" * 32 + '"'}, 1)
    fixture({"data/corpus.zip": "not actually a zip; path test only"}, 1)
    fixture({"src/public.py": "print('V3 is public')\n"}, 0)
    fixture({"results/plan.json": json.dumps({"families": [{"target": "SYNTHETIC", "comms": []}]})}, 1)
    fixture({"results/object.json": json.dumps({"summary": {}, "clauses": [], "docs": []})}, 1)
    fixture({"renamed.json": json.dumps({"publication": "private_runtime_do_not_publish"})}, 1)
    fixture({"PRIVATE_RUNTIME_OWNER": "indra-runtime-v1\n"}, 1)
    fixture({".venv/benign.py": "# never part of a source export\n"}, 1)
    fixture({"__pycache__/cache.pyc": "synthetic cache"}, 1)
    fixture({"data/page.json": json.dumps({"id": "SYNTHETIC", "docs": [], "paras": []})}, 0)
    fixture({"schema.json": json.dumps({"type": "object", "properties": {"families": {"type": "array"}}})}, 0)
    with tempfile.TemporaryDirectory(prefix="indra-boundary-links-") as temp:
        root = Path(temp)
        (root / "directory").mkdir()
        (root / "linked-directory").symlink_to(root / "directory", target_is_directory=True)
        (root / "dangling-file").symlink_to(root / "absent")
        with contextlib.redirect_stdout(io.StringIO()):
            assert check(root, proposal=True) == 1
    with tempfile.TemporaryDirectory(prefix="indra-boundary-index-") as temp:
        root = Path(temp)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        source = root / "renamed.py"
        source.write_text("v4_locate.py" + " — 注经引擎 v4", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "renamed.py"], check=True)
        source.write_text("print('benign working copy')\n")
        with contextlib.redirect_stdout(io.StringIO()):
            assert check(root, staged=True) == 1, "real index blob must be inspected"
        subprocess.run(["git", "-C", str(root), "add", "renamed.py"], check=True)
        with contextlib.redirect_stdout(io.StringIO()):
            assert check(root, staged=True) == 0
    print("PASS: source, private JSON, index/working-copy mismatch, symlinks and cache boundary cases")


if __name__ == "__main__":
    main()
