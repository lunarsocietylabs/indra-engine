#!/usr/bin/env python3
# Copyright (C) 2026 Lunar Society Technologies Co., Ltd.
"""Public code disclosure guard. No credentials, private V4, or corpus archives.

Default: scan git tracked files in indra-engine (CI/checkout).
Optional: --scan-dir DIR to check a candidate export tree BEFORE publication.
Neither mode substitutes for mandatory PR review / repository branch rules.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_PUBLIC_V4 = {"indra/v4_batch.py"}

PRIVATE_PREFIXES = (
    "engine/v4/", "private/", "proprietary/", "internal/private/",
    "docs/code-sync/private/", "data/private/", "artifacts/private/",
    "recovery/claude-export/", "tmp/engine-v4/",
)
SOURCE_PATHS = re.compile(r"^indra/v4_[A-Za-z0-9_]+[.]py$")
TEST_PATHS = re.compile(r"^(?:tests/|indra/tests/)test_v4_[A-Za-z0-9_]+[.]py$")
SENSITIVE_NAMES = re.compile(
    r"(?i)(?:^|/)(?:[.]env(?:[.]|$)|id_rsa|id_ed25519|"
    r"[^/]+[.](?:pem|p12|pfx|key|safetensors|gguf|onnx|"
    r"pt|pth|pickle|pkl|parquet|sqlite|db|npz|npy|"
    r"tar|tgz|zip|7z|zst|r2|multipart))$"
)
SECRET_PATTERNS = (
    ("PEM private key", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("GitHub token", re.compile(rb"(?:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{35,})")),
    ("AWS access key", re.compile(rb"AKIA[0-9A-Z]{16}")),
    ("embedded Cloudflare credential", re.compile(
        rb"(?im)^[ \t]*(?:CF_R2_TOKEN|CF_UPLOAD_TOKEN|CLOUDFLARE_API_TOKEN)"
        rb"[ \t]*[:=][ \t]*['\"][A-Za-z0-9_-]{24,}['\"]"
    )),
    ("embedded model API key", re.compile(
        rb"(?im)^[ \t]*(?:DEEPSEEK_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY)"
        rb"[ \t]*[:=][ \t]*['\"][A-Za-z0-9_-]{24,}['\"]"
    )),
)

# Construct guarded signatures in pieces so this scanner does not contain
# their literal source strings. Covers common rename/move disclosure errors,
# not adversarial obfuscation or unseen proprietary source.
PRIVATE_SOURCE_MARKERS = (
    b"v4_locate.py" + " — 注经引擎 v4".encode(),
    b"v4_run.py" + " — 在真实语料上跑".encode(),
    b"v4_plan.py" + " — 全藏".encode(),
    b"v4_grammar.py" + " — 略牒文法".encode(),
    b"v4_kepan.py" + " — 科判树".encode(),
    b"v4_layers.py" + " — 层层相注".encode(),
    b"v4_relation.py" + " — 经疏关系从数据里来".encode(),
    b"v4_cite.py" + " — 以经注经引证图".encode(),
    b"v4_parallel.py" + " — 同本异译对齐".encode(),
    b"def thresholds_all" + b"(true_pairs, decoy_pairs",
    b"def compose" + b"(b_to_a, a_to_s",
    b"def cite_graph" + b"(clause_segs, texts",
    b"def run_family" + b"(fam, tid, title",
    b"def find_links" + b"(raw, S, expect",
    b"def kmer_for" + b"(lenS",
)

def tracked_files(root: Path) -> list[Path]:
    raw = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"])
    return sorted(root / p.decode("utf-8", "replace") for p in raw.split(b"\0") if p)

def proposed_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*")
                  if p.is_file() and not any(x in {".git", ".venv", "__pycache__", ".pytest_cache"} for x in p.relative_to(root).parts))

def check(root: Path, proposal: bool) -> int:
    files = proposed_files(root) if proposal else tracked_files(root)
    failures: list[tuple[str, str]] = []
    checked = 0
    for path in files:
        rel = path.relative_to(root).as_posix()
        if any(rel.startswith(x) for x in PRIVATE_PREFIXES):
            failures.append((rel, "private source/data path"))
        if SOURCE_PATHS.fullmatch(rel) and rel not in ALLOWED_PUBLIC_V4:
            failures.append((rel, "V4 implementation filename"))
        if TEST_PATHS.fullmatch(rel):
            failures.append((rel, "V4 core test filename"))
        if SENSITIVE_NAMES.search(rel):
            failures.append((rel, "private archive/credential filename"))
        if path.is_symlink() or not path.is_file():
            failures.append((rel, "symlink or inaccessible file"))
            continue
        if path.stat().st_size > 2_000_000:
            failures.append((rel, "oversize tracked file (manual review)"))
            continue
        raw = path.read_bytes()
        checked += 1
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(raw):
                failures.append((rel, label))
        for signature in PRIVATE_SOURCE_MARKERS:
            if signature in raw:
                failures.append((rel, "private V4 code signature"))
                break
    if failures:
        print("FAIL: possible public disclosure. Check all paths; no secret values are printed.")
        for path, why in failures:
            print(" -", path, ":", why)
        return 1
    print(f"PASS: {checked} files scanned; known private code signatures, forbidden files and credential patterns absent.")
    return 0

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--scan-dir", type=Path, default=None,
                   help="scan every candidate file in a directory before publication")
    args = p.parse_args()
    root = args.scan_dir.resolve() if args.scan_dir else ROOT
    if not root.is_dir():
        p.error(f"not a directory: {root}")
    return check(root, proposal=args.scan_dir is not None)

if __name__ == "__main__":
    raise SystemExit(main())
