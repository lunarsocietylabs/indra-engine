#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Run a private module in its isolated runtime; publish only fixed status text."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import oidc_store as store


def tag(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value) or value in {".", ".."}:
        raise ValueError("invalid private storage tag")
    return value


def number(name: str, default: str, maximum: int) -> int:
    value = int(os.environ.get(name, default))
    if not 1 <= value <= maximum:
        raise ValueError("invalid resource limit")
    return value


def upload(path: Path, remote: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError("output is not a regular file")
    data = path.read_bytes()
    size = 7_500_000
    if len(data) <= size:
        store.put(remote, data)
    else:
        for offset in range(0, len(data), size):
            store.put(remote + ".part%03d" % (offset // size), data[offset:offset + size])


def run(mode: str) -> int:
    runtime = Path(os.environ["INDRA_RUNTIME"])
    if runtime.is_symlink() or not (runtime / "PRIVATE_RUNTIME_PROVENANCE.json").is_file():
        raise ValueError("verified private runtime is absent")
    runtime = runtime.resolve(strict=True)
    if any((p / ".git").exists() for p in [runtime, *runtime.parents]):
        raise ValueError("runtime is inside checkout")
    run_id = tag(os.environ["GITHUB_RUN_ID"])
    logs = runtime / "private-logs"
    inputs = runtime / "private-inputs"
    output = runtime / "private-output"
    for path in (logs, inputs, output):
        path.mkdir(mode=0o700, exist_ok=True)

    def invoke(module: str, args: list[str], label: str) -> None:
        with (logs / (label + ".log")).open("wb") as handle:
            result = subprocess.run([sys.executable, "-m", module, *args], cwd=runtime,
                                    stdout=handle, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError("private module failed")

    status = 0
    upload_prefix = None
    shard = os.environ.get("SHARD", "0")
    if not re.fullmatch(r"[0-7]", shard):
        raise ValueError("invalid shard")
    try:
        if mode == "plan":
            args = ["--out", str(output), "--classes", os.environ.get("PLAN_CLASSES", "canon")]
            previous = os.environ.get("UNMATCHED_TAG", "")
            if previous:
                path = inputs / "previous-plan.json"
                path.write_bytes(store.get("engine-v4-out/" + tag(previous) + "/plan.json"))
                args += ["--unmatched-from", str(path)]
            layers = [x for x in os.environ.get("LAYER_TAGS", "").split(",") if x]
            layer_paths = []
            for index, item in enumerate(layers):
                path = inputs / ("layer-%03d.json" % index)
                path.write_bytes(store.get("engine-v4-out/" + tag(item) + "/plan.json"))
                layer_paths.append(str(path))
            if layer_paths:
                args += ["--layer-from", ",".join(layer_paths)]
            invoke("indra.v4_plan", args, "plan")
            upload(output / "plan.json", "engine-v4-out/" + run_id + "/plan.json")
        elif mode == "batch":
            plan_tag = tag(os.environ.get("PLAN_TAG") or run_id)
            out_tag = tag(os.environ.get("OUT_TAG") or plan_tag)
            upload_prefix = "engine-v4-out/" + out_tag + "/"
            path = inputs / "plan.json"
            path.write_bytes(store.get("engine-v4-out/" + plan_tag + "/plan.json"))
            nshards = number("NSHARDS", "8", 8)
            if int(shard) >= nshards:
                raise ValueError("shard exceeds requested shard count")
            if shard == "0":
                shutil.copyfile(path, output / "plan.json")
            invoke("indra.v4_batch", ["--plan", str(path), "--shard", shard,
                   "--nshards", str(nshards), "--only", os.environ.get("ONLY", ""),
                   "--workers", str(number("WORKERS", "2", 8)), "--out", str(output)], "batch")
            result = json.loads((output / ("_shard" + shard + ".json")).read_text())
            if result.get("errors"):
                raise RuntimeError("one or more private batch targets failed")
        else:
            upload_prefix = "engine-v4-out/" + run_id + "/shell/"
            invoke("tests.test_v4_locate", [], "test-locate")
            invoke("tests.test_v4_modules", [], "test-modules")
            invoke("indra.v4_run", ["--out", str(output), "--only", os.environ.get("ONLY", "heart"),
                   "--extra", os.environ.get("EXTRA", ""),
                   "--workers", str(number("WORKERS", "4", 8))], "shell")
    except Exception:
        status = 1
    finally:
        try:
            if upload_prefix:
                for path in sorted(output.iterdir()):
                    if path.is_file():
                        upload(path, upload_prefix + path.name)
            log_prefix = "engine-v4-out/" + run_id + "/logs/" + mode + "-" + shard + "/"
            for path in sorted(logs.iterdir()):
                upload(path, log_prefix + path.name)
        except Exception:
            status = 1
    print("PASS: private job completed; details stored privately." if status == 0
          else "FAIL: private job failed; any retained diagnostics are restricted.")
    return status


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["plan", "batch", "shell"])
    args = parser.parse_args()
    try:
        raise SystemExit(run(args.mode))
    except Exception:
        print("FAIL: private job configuration rejected; no payload was printed.", file=sys.stderr)
        raise SystemExit(2)
