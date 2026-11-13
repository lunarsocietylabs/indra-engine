#!/usr/bin/env python3
"""Non-sensitive regression fixtures for the public disclosure guard.

Runs on the public standard GitHub Runner. Never contains real credentials or
private source code. Test samples are synthetic and generated in a temp dir.
"""
from __future__ import annotations

import contextlib
import io
from pathlib import Path
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
    print("PASS: public disclosure guard accepted benign files and rejected 7 synthetic hazards")


if __name__ == "__main__":
    main()
