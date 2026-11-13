#!/usr/bin/env python3
# Copyright (C) 2026 Lunar Society Technologies Co., Ltd.
"""Public repository disclosure guard for indra-engine.

This checks the *currently tracked* source tree and helps catch accidental
publication of private V4 implementations or credential literals. It is not
a replacement for mandatory PR review and GitHub branch protection: a direct
push is already public before the post-push check runs.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_V4_PUBLIC = {"indra/v4_batch.py"}
PRIVATE_PREFIXES = (
    "engine/v4/",
    "private/",
    "proprietary/",
    "internal/private/",
    "docs/code-sync/private/",
    "data/private/",
    "artifacts/private/",
)
BANNED_V4_MODULE = re.compile(r"^indra/v4_[A-Za-z0-9_]+\.py$")
BANNED_V4_TEST = re.compile(r"^(?:tests/|indra/tests/)test_v4_[A-Za-z0-9_]+\.py$")
CREDENTIAL_FILE = re.compile(r"(?i)(?:^|/)(?:id_rsa|id_ed25519|.*\.(?:pem|p12|pfx|key))$")
SECRET_PATTERNS = {
    "PEM private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "GitHub personal token": re.compile(rb"(?:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{35,})"),
    "Cloudflare API token literal": re.compile(
        rb"(?im)^\s*(?:CF_R2_TOKEN|CF_UPLOAD_TOKEN|CLOUDFLARE_API_TOKEN)\s*[:=]\s*['\"][A-Za-z0-9_-]{24,}['\"]"
    ),
    "LLM API key literal": re.compile(
        rb"(?im)^\s*(?:DEEPSEEK_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY)\s*[:=]\s*['\"][A-Za-z0-9_-]{24,}['\"]"
    ),
}


def tracked_files() -> list[str]:
    raw = subprocess.check_output(["git", "-C", str(ROOT), "ls-files", "-z"])
    return sorted(p.decode("utf-8", "replace") for p in raw.split(b"\0") if p)


def check() -> int:
    errors: list[tuple[str, str]] = []
    checked = 0
    for rel in tracked_files():
        if any(rel.startswith(prefix) for prefix in PRIVATE_PREFIXES):
            errors.append((rel, "protected private directory"))
        if BANNED_V4_MODULE.fullmatch(rel) and rel not in ALLOWED_V4_PUBLIC:
            errors.append((rel, "private V4 implementation in public repo"))
        if BANNED_V4_TEST.fullmatch(rel):
            errors.append((rel, "private V4 test in public repo"))
        if CREDENTIAL_FILE.search(rel):
            errors.append((rel, "credential-shaped file path"))
        path = ROOT / rel
        if not path.is_file() or path.is_symlink():
            continue
        if path.stat().st_size > 2_000_000:
            errors.append((rel, "oversized tracked file requires review"))
            continue
        raw = path.read_bytes()
        checked += 1
        # Do not print matching source lines, captured tokens, or secret values.
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(raw):
                errors.append((rel, name))
    if errors:
        print("FAIL: publication boundary needs review.")
        for rel, kind in errors:
            print(" -", rel, ":", kind)
        return 1
    print(
        f"PASS: {checked} tracked files checked; "
        "no forbidden private V4 paths or recognized credential literals."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(check())
