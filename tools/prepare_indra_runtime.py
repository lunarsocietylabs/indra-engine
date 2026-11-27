#!/usr/bin/env python3
# Copyright (C) 2026 Lunar Society Technologies Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0-only
"""Assemble a private runtime outside every checkout; never publish its output.

Only the pinned public Git commit and explicitly scoped private Python source
are copied. Existing license notices remain unchanged. This is an engineering
boundary, not a determination of copyright ownership or license compliance.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile

RECEIPT = "PRIVATE_RUNTIME_PROVENANCE.json"
MAX_FILE_BYTES = 8_000_000
MAX_ARCHIVE_BYTES = 64_000_000


class BoundaryError(ValueError):
    """A candidate did not meet the runtime's source or destination boundary."""


def relative_name(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise BoundaryError("invalid relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or any(p == ".git" for p in path.parts):
        raise BoundaryError("unsafe relative path")
    cleaned = path.as_posix().rstrip("/")
    if cleaned in {"", "."}:
        raise BoundaryError("empty relative path")
    return cleaned


def load_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise BoundaryError("unsupported runtime manifest")
    public = manifest.get("public_base", {})
    if public.get("repository") != "lunarsocietylabs/indra-engine":
        raise BoundaryError("unexpected public repository")
    if not re.fullmatch(r"[0-9a-f]{40}", public.get("commit", "")):
        raise BoundaryError("public commit must be a full SHA")
    root = relative_name(manifest.get("private_overlay", {}).get("root", ""))
    if root != "engine/v4":
        raise BoundaryError("unexpected private source scope")
    return manifest


def tar_files(blob: bytes) -> dict[str, bytes]:
    if len(blob) > MAX_ARCHIVE_BYTES:
        raise BoundaryError("source archive exceeds size limit")
    files: dict[str, bytes] = {}
    seen: set[str] = set()
    total = 0
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as archive:
        for member in archive:
            # Some older tar producers emit a harmless top-level ./ directory.
            if member.isdir() and member.name in {".", "./"}:
                continue
            name = relative_name(member.name)
            if name in seen:
                raise BoundaryError("duplicate archive member")
            seen.add(name)
            if member.isdir():
                continue
            if not member.isfile():
                raise BoundaryError("archive links and special files are forbidden")
            if member.size > MAX_FILE_BYTES or member.size < 0:
                raise BoundaryError("source file exceeds size limit")
            total += member.size
            if total > MAX_ARCHIVE_BYTES:
                raise BoundaryError("expanded source exceeds size limit")
            stream = archive.extractfile(member)
            if stream is None:
                raise BoundaryError("unreadable archive member")
            content = stream.read(MAX_FILE_BYTES + 1)
            if len(content) != member.size:
                raise BoundaryError("archive member size mismatch")
            files[name] = content
    return files


def public_snapshot(root: Path, commit: str) -> dict[str, bytes]:
    root = root.resolve(strict=True)
    found = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "--verify", commit + "^{commit}"],
        stderr=subprocess.DEVNULL, text=True,
    ).strip()
    if found != commit:
        raise BoundaryError("public commit did not resolve exactly")
    blob = subprocess.check_output(
        ["git", "-C", str(root), "archive", "--format=tar", commit],
        stderr=subprocess.DEVNULL,
    )
    files = tar_files(blob)
    if b"GNU AFFERO GENERAL PUBLIC LICENSE" not in files.get("LICENSE", b""):
        raise BoundaryError("public license is missing")
    if "indra/__init__.py" not in files:
        raise BoundaryError("public Python package is missing")
    return files


def private_source_allowed(name: str) -> bool:
    path = PurePosixPath(relative_name(name))
    if any(p.startswith(".") or p == "__pycache__" for p in path.parts):
        return False
    # Current private source contains Python modules, tools and tests. Corpus
    # data, model files, credentials and generated output are never copied.
    if path.suffix == ".py":
        return len(path.parts) == 1 or path.parts[0] in {"indra", "tests"}
    if path.parts[0] == "docs" and path.suffix in {".md", ".rst", ".txt"}:
        return True
    if path.parts[:2] == ("tests", "fixtures") and path.suffix in {".json", ".txt"}:
        return True
    return len(path.parts) == 1 and path.name in {
        "README.md", "README.rst", "LICENSE", "LICENSE.txt", "NOTICE", "NOTICE.md",
    }


def private_from_directory(root: Path, scope: str) -> dict[str, bytes]:
    for ancestor in [root.absolute(), *root.absolute().parents]:
        if ancestor.is_symlink():
            raise BoundaryError("private source root contains a symlink")
    root = root.resolve(strict=True)
    current = root
    for part in PurePosixPath(scope).parts:
        current = current / part
        if current.is_symlink():
            raise BoundaryError("private source scope contains a symlink")
    if not current.is_dir():
        raise BoundaryError("private source scope is absent")
    files: dict[str, bytes] = {}
    for directory, dirs, names in os.walk(current, followlinks=False):
        folder = Path(directory)
        for entry in [*dirs, *names]:
            if (folder / entry).is_symlink():
                raise BoundaryError("private source contains a symlink")
        # Generated Python caches are neither source nor a publication input.
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for entry in names:
            source = folder / entry
            name = source.relative_to(current).as_posix()
            if not private_source_allowed(name) or not source.is_file():
                raise BoundaryError("private source contains a non-source file")
            if source.stat().st_size > MAX_FILE_BYTES:
                raise BoundaryError("private source file exceeds size limit")
            files[name] = source.read_bytes()
    return files


def private_from_archive(path: Path, scope: str) -> dict[str, bytes]:
    if path.is_symlink() or not path.is_file():
        raise BoundaryError("private archive must be a regular file")
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise BoundaryError("private archive exceeds size limit")
    files = tar_files(path.read_bytes())
    # Current packages use engine/v4/. Earlier packages may have one outer
    # directory, or place indra/ and tests/ at the root. Select exactly one
    # source root, anchored by the actual engine entry module.
    anchors = []
    for name in files:
        if name.endswith("indra/v4_run.py"):
            prefix = name[: -len("indra/v4_run.py")].rstrip("/")
            if prefix in {"", scope} or prefix.endswith("/" + scope):
                anchors.append(prefix)
    if len(anchors) != 1:
        raise BoundaryError("private archive source root is ambiguous or absent")
    prefix = anchors[0] + "/" if anchors[0] else ""
    chosen = {name[len(prefix):]: content for name, content in files.items() if name.startswith(prefix)}
    if not all(private_source_allowed(name) for name in chosen):
        raise BoundaryError("private archive scope contains a non-source file")
    return chosen


def validate_destination(output: Path, source_roots: list[Path]) -> Path:
    # Reject symlink ancestors before resolving, including dangling links.
    absolute = output.absolute()
    for ancestor in [absolute, *absolute.parents]:
        if ancestor.is_symlink():
            raise BoundaryError("runtime destination contains a symlink")
    resolved = absolute.resolve()
    if resolved.exists():
        raise BoundaryError("runtime output must be a new directory")
    for ancestor in [resolved, *resolved.parents]:
        if (ancestor / ".git").exists():
            raise BoundaryError("runtime output cannot be inside a Git checkout")
    for root in source_roots:
        source = root.resolve()
        if resolved == source or source in resolved.parents or resolved in source.parents:
            raise BoundaryError("runtime output cannot overlap source input")
    return resolved


def assemble(public_root: Path, manifest_path: Path, output: Path,
             private_root: Path | None = None, private_archive: Path | None = None) -> dict:
    if (private_root is None) == (private_archive is None):
        raise BoundaryError("choose exactly one private source input")
    manifest = load_manifest(manifest_path)
    sources = [public_root] + ([private_root] if private_root is not None else [])
    destination = validate_destination(output, sources)
    public = public_snapshot(public_root, manifest["public_base"]["commit"])
    scope = manifest["private_overlay"]["root"]
    private = (private_from_directory(private_root, scope) if private_root is not None
               else private_from_archive(private_archive, scope))
    if not {"indra/v4_run.py", "indra/v4_locate.py"}.issubset(private):
        raise BoundaryError("private engine entry modules are missing")
    if sum(map(len, private.values())) > MAX_ARCHIVE_BYTES:
        raise BoundaryError("private source exceeds size limit")
    files = dict(public)
    for name, content in private.items():
        files[scope + "/" + name] = content
        if name.startswith(("indra/", "tests/")) and name.endswith(".py"):
            files[name] = content
    files.setdefault("tests/__init__.py", b"")
    private_hashes = {name: hashlib.sha256(content).hexdigest() for name, content in sorted(private.items())}
    receipt = {
        "schema_version": 1, "publication": "private_runtime_do_not_publish",
        "public_base": manifest["public_base"], "private_scope": scope,
        "private_input": "directory" if private_root is not None else "archive",
        "private_files_sha256": private_hashes,
        "private_tree_sha256": hashlib.sha256(json.dumps(private_hashes, sort_keys=True).encode()).hexdigest(),
        "license_status": "existing_notices_preserved_no_relicensing",
        "files_sha256": {name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())},
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir(mode=0o700)
    try:
        owner = destination / "PRIVATE_RUNTIME_OWNER"
        owner.write_text("indra-runtime-v1\n", encoding="utf-8")
        owner.chmod(0o600)
        for name, content in files.items():
            path = destination / relative_name(name)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.write_bytes(content)
            path.chmod(0o600)
        marker = destination / RECEIPT
        marker.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        marker.chmod(0o600)
    except BaseException:
        shutil.rmtree(destination)
        raise
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-root", type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--private-root", type=Path)
    group.add_argument("--private-archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--github-env", type=Path)
    args = parser.parse_args(argv)
    env_handle = None
    assembled = False
    completed = False
    try:
        value = str(args.output.absolute())
        if "\n" in value or "\r" in value:
            raise BoundaryError("runtime path cannot contain a newline")
        if args.github_env:
            env_path = args.github_env.absolute()
            if "\n" in str(env_path) or "\r" in str(env_path):
                raise BoundaryError("environment path cannot contain a newline")
            for ancestor in [env_path, *env_path.parents]:
                if ancestor.is_symlink():
                    raise BoundaryError("environment file cannot use symlinks")
            # Open before touching the runtime. Missing/unwritable env parents
            # must not leave a private runtime that later jobs cannot locate.
            env_handle = env_path.open("a", encoding="utf-8")
        assemble(args.public_root, args.manifest, args.output, args.private_root, args.private_archive)
        assembled = True
        if env_handle:
            env_handle.write("INDRA_RUNTIME=" + str(args.output.resolve()) + "\n")
            env_handle.flush()
            os.fsync(env_handle.fileno())
            env_handle.close()
            env_handle = None
        completed = True
        print("PASS: pinned public base and private source assembled outside checkouts; notices preserved.")
        return 0
    except (BoundaryError, OSError, ValueError, subprocess.SubprocessError, tarfile.TarError) as exc:
        # Exception details may contain private file paths or archive payloads.
        print("FAIL: runtime assembly rejected (" + type(exc).__name__ + ").", file=sys.stderr)
        return 2
    finally:
        try:
            if env_handle:
                try:
                    env_handle.close()
                except OSError:
                    # A failed flush can fail again on close. The original
                    # operation already returned a fixed failure message.
                    pass
        finally:
            if assembled and not completed:
                shutil.rmtree(args.output)


if __name__ == "__main__":
    raise SystemExit(main())
