#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Remove only this workflow's fixed runtime and private download directories."""
import os
from pathlib import Path
import shutil


def main() -> None:
    root = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    if any((p / ".git").exists() for p in [root, *root.parents]):
        raise ValueError("cleanup root cannot be a Git checkout")
    candidates = [root / "indra-runtime", *root.glob("indra-private-download-*")]
    for path in candidates:
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            if any(path.rglob(".git")):
                raise ValueError("cleanup refuses a directory containing a checkout")
            if path.name == "indra-runtime":
                marker = path / "PRIVATE_RUNTIME_OWNER"
                if not marker.is_file() or marker.read_text() != "indra-runtime-v1\n":
                    raise ValueError("cleanup refuses a runtime without its ownership marker")
            shutil.rmtree(path)
    print("private runtime cleanup complete")


if __name__ == "__main__":
    main()
