#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""No-network runner tests using synthetic modules and in-memory private storage."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / ".github/scripts"
sys.path.insert(0, str(SCRIPTS))
import oidc_store as store
import run_private_job as job
import v4_report as report

SENTINEL = "SYNTHETIC_PRIVATE_PAYLOAD_MUST_NOT_BE_PUBLIC"
STUB = """import argparse, json, os
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--out');p.add_argument('--shard',default='0')
a,_=p.parse_known_args()
print('SYNTHETIC_PRIVATE_PAYLOAD_MUST_NOT_BE_PUBLIC')
if os.environ.get('SYNTHETIC_FAIL') == '1': raise SystemExit(7)
out=Path(a.out);out.mkdir(exist_ok=True)
(out/'plan.json').write_text(json.dumps({'families':[]}))
(out/('_shard'+a.shard+'.json')).write_text(json.dumps({'done':1,'errors':[]}))
(out/'result.json').write_text(json.dumps({'synthetic':'SYNTHETIC_PRIVATE_PAYLOAD_MUST_NOT_BE_PUBLIC'}))
"""


class RunnerTests(unittest.TestCase):
    def make_runtime(self, root):
        runtime = root / "indra-runtime"
        for name in ["indra", "tests"]:
            (runtime / name).mkdir(parents=True)
            (runtime / name / "__init__.py").write_text("")
        for name in ["v4_run.py", "v4_plan.py", "v4_batch.py"]:
            (runtime / "indra" / name).write_text(STUB)
        for name in ["test_v4_locate.py", "test_v4_modules.py"]:
            (runtime / "tests" / name).write_text("print(" + repr(SENTINEL) + ")\n")
        (runtime / "PRIVATE_RUNTIME_PROVENANCE.json").write_text("{}")
        return runtime

    def exercise(self, mode, fail=False, fail_storage=False):
        with tempfile.TemporaryDirectory(prefix="indra-runner-test-") as directory:
            root = Path(directory)
            runtime = self.make_runtime(root)
            captured = []
            def put(path, data, *args):
                if fail_storage:
                    raise OSError("synthetic storage failure")
                captured.append((path, data))
            env = {"INDRA_RUNTIME": str(runtime), "GITHUB_RUN_ID": "123", "SHARD": "0",
                   "NSHARDS": "1", "WORKERS": "1", "PLAN_TAG": "", "OUT_TAG": "",
                   "LAYER_TAGS": "", "UNMATCHED_TAG": "", "SYNTHETIC_FAIL": "1" if fail else "0",
                   "PYTHONDONTWRITEBYTECODE": "1"}
            output = io.StringIO()
            with patch.dict(os.environ, env), patch.object(job.store, "get", return_value=b'{"families":[]}'), \
                 patch.object(job.store, "put", side_effect=put), contextlib.redirect_stdout(output):
                status = job.run(mode)
            self.assertNotIn(SENTINEL, output.getvalue())
            self.assertEqual(status, 1 if fail or fail_storage else 0)
            if not fail_storage:
                self.assertTrue(any(path.endswith(".log") and SENTINEL.encode() in data for path, data in captured))

    def test_all_modes_keep_payload_off_stdout(self):
        for mode in ["plan", "batch", "shell"]:
            with self.subTest(mode=mode):
                self.exercise(mode)

    def test_module_failure_keeps_diagnostics_private(self):
        self.exercise("shell", fail=True)

    def test_storage_failure_does_not_claim_success(self):
        self.exercise("batch", fail_storage=True)

    def test_download_preserves_explicit_legacy_prefix_without_extracting(self):
        with tempfile.TemporaryDirectory(prefix="indra-download-test-") as directory:
            root = Path(directory)
            blob = b"synthetic archive bytes; extraction belongs to the assembler"
            manifest = {"parts": 1, "tar_sha256": hashlib.sha256(blob).hexdigest()}
            calls = []
            def get(path):
                calls.append(path)
                return json.dumps(manifest).encode() if path.endswith("MANIFEST.json") else blob
            with patch.dict(os.environ, {"RUNNER_TEMP": str(root), "GITHUB_ENV": str(root / "env")}), \
                 patch.object(store, "get", side_effect=get), contextlib.redirect_stdout(io.StringIO()):
                archive = store.download_engine("engine-v4")
            self.assertEqual(calls, ["engine-v4/MANIFEST.json", "engine-v4/part-000"])
            self.assertEqual(archive.read_bytes(), blob)
            self.assertFalse((root / "indra").exists())
            self.assertEqual(archive.parent.stat().st_mode & 0o777, 0o700)

    def test_damaged_private_gzip_does_not_escape_in_report_error(self):
        responses = [json.dumps({"families": [{"target": "SYNTHETIC"}]}).encode(),
                     SENTINEL.encode()]
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["v4_report.py", "123"]), \
             patch.object(report.S, "get", side_effect=responses), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            status = report.cli()
        self.assertEqual(status, 2)
        self.assertNotIn(SENTINEL, output.getvalue() + errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())
        self.assertEqual(errors.getvalue(), "FAIL: private report failed; no response payload was printed.\n")


if __name__ == "__main__":
    unittest.main()
