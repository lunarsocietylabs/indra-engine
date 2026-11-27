#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic boundary tests; never fetch real engine bundles or corpus data."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import prepare_indra_runtime as runtime


class RuntimeBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="indra-runtime-test-")
        self.root = Path(self.temp.name)
        self.public = self.root / "public"
        self.public.mkdir()
        subprocess.run(["git", "init", "-q", str(self.public)], check=True)
        (self.public / "indra").mkdir()
        (self.public / "indra/__init__.py").write_text("")
        (self.public / "indra/norm.py").write_text("VERSION = 'pinned-public'\n")
        (self.public / "LICENSE").write_text("GNU AFFERO GENERAL PUBLIC LICENSE\nsynthetic test only\n")
        self.git("add", ".")
        self.git("-c", "user.name=Boundary Test", "-c", "user.email=boundary@example.invalid", "commit", "-qm", "fixture")
        self.commit = self.git("rev-parse", "HEAD").strip()
        self.private = self.root / "private"
        self.source = self.private / "engine/v4"
        (self.source / "indra").mkdir(parents=True)
        (self.source / "tests").mkdir()
        (self.source / "indra/v4_run.py").write_text("# synthetic private entry\n")
        (self.source / "indra/v4_locate.py").write_text("# synthetic private locate\n")
        (self.source / "tests/test_engine.py").write_text("# synthetic private test\n")
        (self.source / "helper.py").write_text("# synthetic private CLI\n")
        self.manifest = self.root / "manifest.json"
        self.manifest.write_text(json.dumps({"schema_version": 1,
            "public_base": {"repository": "lunarsocietylabs/indra-engine", "commit": self.commit},
            "private_overlay": {"root": "engine/v4"}}))
        self.output = self.root / "runtime"

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.public), *args], text=True)

    def assemble(self, **kwargs):
        return runtime.assemble(self.public, self.manifest, kwargs.pop("output", self.output),
                                private_root=self.private, **kwargs)

    def test_pinned_runtime_preserves_sources_and_license(self):
        (self.public / "indra/norm.py").write_text("VERSION = 'uncommitted-change'\n")
        status = self.git("status", "--porcelain=v1")
        receipt = self.assemble()
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.output / "LICENSE").read_bytes(), (self.public / "LICENSE").read_bytes())
        self.assertIn("pinned-public", (self.output / "indra/norm.py").read_text())
        self.assertEqual((self.output / "engine/v4/helper.py").read_bytes(), (self.source / "helper.py").read_bytes())
        self.assertEqual((self.output / "indra/v4_run.py").read_bytes(), (self.source / "indra/v4_run.py").read_bytes())
        self.assertEqual(status, self.git("status", "--porcelain=v1"))
        self.assertFalse((self.public / "indra/v4_run.py").exists())
        self.assertEqual(receipt["public_base"]["commit"], self.commit)

    def test_wrong_commit_rejected_before_output(self):
        manifest = json.loads(self.manifest.read_text())
        manifest["public_base"]["commit"] = "0" * 40
        self.manifest.write_text(json.dumps(manifest))
        with self.assertRaises(subprocess.SubprocessError):
            self.assemble()
        self.assertFalse(self.output.exists())

    def test_checkout_and_source_destinations_rejected(self):
        for output in [self.public / "runtime", self.private / "runtime", self.root]:
            with self.subTest(output=str(output)), self.assertRaises(runtime.BoundaryError):
                self.assemble(output=output)

    def test_source_symlink_and_unknown_payload_rejected(self):
        link = self.source / "tests/linked.py"
        link.symlink_to(self.source / "helper.py")
        with self.assertRaises(runtime.BoundaryError):
            self.assemble()
        link.unlink()
        (self.source / "corpus.json").write_text('{"synthetic":true}')
        with self.assertRaises(runtime.BoundaryError):
            self.assemble()
        self.assertFalse(self.output.exists())

    def test_legacy_engine_tar_preserves_layout(self):
        archive = self.root / "private.tar.gz"
        with tarfile.open(archive, "w:gz") as handle:
            handle.add(self.private / "engine", arcname="engine")
        receipt = runtime.assemble(self.public, self.manifest, self.output, private_archive=archive)
        self.assertEqual(receipt["private_input"], "archive")
        self.assertTrue((self.output / "engine/v4/tests/test_engine.py").exists())

    def test_malicious_archive_members_rejected(self):
        for kind in ["traversal", "absolute", "symlink", "duplicate"]:
            with self.subTest(kind=kind):
                data = io.BytesIO()
                with tarfile.open(fileobj=data, mode="w") as archive:
                    member = tarfile.TarInfo("../escape" if kind == "traversal" else "/absolute" if kind == "absolute" else "safe.py")
                    if kind == "symlink":
                        member.type = tarfile.SYMTYPE
                        member.linkname = "outside"
                        archive.addfile(member)
                    else:
                        member.size = 1
                        archive.addfile(member, io.BytesIO(b"x"))
                        if kind == "duplicate":
                            archive.addfile(member, io.BytesIO(b"y"))
                with self.assertRaises(runtime.BoundaryError):
                    runtime.tar_files(data.getvalue())

    def cli(self, *extra):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return runtime.main(["--public-root", str(self.public), "--private-root", str(self.private),
                                 "--manifest", str(self.manifest), "--output", str(self.output), *extra])

    def test_bad_env_path_prevents_creation(self):
        self.assertEqual(self.cli("--github-env", str(self.root / "absent/env")), 2)
        self.assertFalse(self.output.exists())

    def test_newline_output_prevents_creation(self):
        self.output = self.root / "runtime\nunsafe"
        self.assertEqual(self.cli(), 2)
        self.assertFalse(self.output.exists())

    def test_failed_env_write_cleans_created_runtime(self):
        with patch.object(runtime.os, "fsync", side_effect=OSError("synthetic write failure")):
            self.assertEqual(self.cli("--github-env", str(self.root / "env")), 2)
        self.assertFalse(self.output.exists())

    def test_flush_and_close_failure_still_cleans_runtime(self):
        env_path = self.root / "env"
        original = Path.open
        class BrokenEnv:
            def write(self, value):
                return len(value)
            def flush(self):
                raise OSError("synthetic full device")
            def close(self):
                raise OSError("synthetic repeated flush failure")
        def opening(path, *args, **kwargs):
            return BrokenEnv() if path == env_path else original(path, *args, **kwargs)
        with patch.object(Path, "open", opening):
            self.assertEqual(self.cli("--github-env", str(env_path)), 2)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
