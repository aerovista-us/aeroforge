import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from aeroforge import sandbox
from aeroforge.storage import connect


class ForgeRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = pathlib.Path(self.temp.name)
        self.repo = self.base / "source"
        self.repo.mkdir()
        self.state = self.base / "state"
        self.env = patch.dict(os.environ, {
            "AEROFORGE_STATE": str(self.state),
            "AEROFORGE_SOURCE_ROOTS": str(self.repo),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        def git(*args):
            return subprocess.check_output(["git", "-C", str(self.repo), *args], text=True).strip()
        self.git = git
        git("init", "-q")
        git("config", "user.name", "AeroForge Fixture")
        git("config", "user.email", "aeroforge@example.invalid")
        (self.repo / "tests").mkdir()
        (self.repo / "tests/test_example.py").write_text(
            "import unittest\nclass Smoke(unittest.TestCase):\n"
            "    def test_ok(self): self.assertTrue(True)\n")
        git("add", ".")
        git("commit", "-qm", "fixture")
        self.commit = git("rev-parse", "HEAD")
        self.conn = connect()
        self.addCleanup(self.conn.close)

    def test_submit_requires_pinned_revision_and_scope(self):
        with self.assertRaises(sandbox.SandboxDenied):
            sandbox.submit(self.conn, self.repo, "HEAD", "python_unittest")
        outside = self.base / "other"
        outside.mkdir()
        with self.assertRaises(sandbox.SandboxDenied):
            sandbox.submit(self.conn, outside, self.commit, "python_unittest")
        jid = sandbox.submit(self.conn, self.repo, self.commit, "python_unittest")
        self.assertEqual(jid, 1)
        self.assertEqual(sandbox.queue_status(self.conn)[0]["status"], "queued")

    def test_run_requires_both_approval_controls(self):
        sandbox.submit(self.conn, self.repo, self.commit, "python_unittest")
        with self.assertRaises(sandbox.SandboxDenied):
            sandbox.run_next(self.conn, approve=False)
        with self.assertRaises(sandbox.SandboxDenied):
            sandbox.run_next(self.conn, approve=True)
        self.assertEqual(sandbox.queue_status(self.conn)[0]["status"], "queued")

    def test_snapshot_is_git_commit_not_working_tree(self):
        (self.repo / "untracked_secret.txt").write_text("DO NOT COPY")
        stage = self.base / "stage"
        info = sandbox._stage_archive(self.repo, self.commit, stage)
        self.assertEqual(info["files"], 1)
        self.assertFalse((stage / "untracked_secret.txt").exists())
        self.assertTrue((stage / "tests/test_example.py").exists())

    def test_secret_files_are_rejected_even_when_committed(self):
        (self.repo / ".env").write_text("SECRET=DO-NOT-COPY")
        self.git("add", ".env")
        self.git("commit", "-qm", "contains secret")
        with self.assertRaises(sandbox.SandboxDenied):
            sandbox._stage_archive(self.repo, self.git("rev-parse", "HEAD"), self.base / "bad")

    def test_symlink_is_rejected(self):
        (self.repo / "leak").symlink_to("/etc/passwd")
        self.git("add", "leak")
        self.git("commit", "-qm", "contains link")
        with self.assertRaises(sandbox.SandboxDenied):
            sandbox._stage_archive(self.repo, self.git("rev-parse", "HEAD"), self.base / "badlink")

    def test_traversal_and_sensitive_paths_denied(self):
        for name in ("/etc/shadow", "src/../etc", ".ssh/id_rsa",
                     "config/credentials", "cert.pem", ".env.production"):
            with self.assertRaises(sandbox.SandboxDenied):
                sandbox._safe_archive_path(name)
        self.assertEqual(str(sandbox._safe_archive_path("tests/test_ok.py")), "tests/test_ok.py")

    def test_docker_policy_exact_quotas_and_no_network(self):
        stage = self.base / "stage"
        stage.mkdir()
        image = "sha256:" + "a" * 64
        args = sandbox.docker_args("test-run", stage, image, 256, .5, 64)
        self.assertIn("--network=none", args)
        self.assertIn("--read-only", args)
        self.assertIn("--memory=256m", args)
        self.assertIn("--memory-swap=256m", args)
        self.assertIn("--cpus=0.5", args)
        self.assertIn("--pids-limit=64", args)
        self.assertIn("--cap-drop=ALL", args)
        self.assertIn("--security-opt=no-new-privileges:true", args)
        self.assertIn("--pull=never", args)
        self.assertIn("--user=65534:65534", args)
        self.assertIn("--log-driver=none", args)
        self.assertEqual(args[-len(sandbox.PROFILES["python_unittest"]):], sandbox.PROFILES["python_unittest"])
        self.assertFalse(any("docker.sock" in x for x in args))

    def test_docker_config_verification_detects_bypasses(self):
        stage = self.base / "stage"
        stage.mkdir()
        conf = {"HostConfig": {"NetworkMode": "none", "PidMode": "", "UTSMode": "", "ReadonlyRootfs": True,
            "Privileged": False, "Memory": 268435456, "MemorySwap": 268435456,
            "NanoCpus": 500000000, "PidsLimit": 64, "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "Mounts": [{"Source": str(stage), "Target": "/workspace", "ReadOnly": True}]},
            "Config": {"User": "65534:65534", "Env": ["PATH=/usr/bin", "HOME=/tmp"]}}
        self.assertEqual(len(sandbox.verify_docker_config(conf, stage, 256, .5, 64)), 14)
        conf["HostConfig"]["NetworkMode"] = "bridge"
        with self.assertRaises(sandbox.SandboxDenied):
            sandbox.verify_docker_config(conf, stage, 256, .5, 64)
        conf["HostConfig"]["NetworkMode"] = "none"
        conf["Config"]["Env"].append("GH_TOKEN=sample-secret")
        with self.assertRaises(sandbox.SandboxDenied):
            sandbox.verify_docker_config(conf, stage, 256, .5, 64)

    def test_resource_guard_blocks_host_pressure(self):
        meminfo = ("MemAvailable: 131072 kB\nSwapTotal: 8388608 kB\n"
                   "SwapFree: 1024 kB\n")
        with patch("pathlib.Path.read_text", return_value=meminfo):
            with self.assertRaises(sandbox.SandboxDenied):
                sandbox.host_preflight(256)
        meminfo = ("MemAvailable: 4194304 kB\nSwapTotal: 8388608 kB\n"
                   "SwapFree: 1024 kB\n")
        with patch("pathlib.Path.read_text", return_value=meminfo):
            with self.assertRaises(sandbox.SandboxDenied):
                sandbox.host_preflight(256)

    def test_limits_reject_unsafe_requests_and_unpinned_image(self):
        stage = self.base / "stage"
        stage.mkdir()
        for mem, cpu, pids, image in [
            (1024, .5, 64, "sha256:" + "b"*64),
            (256, 4., 64, "sha256:" + "b"*64),
            (256, .5, 1024, "sha256:" + "b"*64),
            (256, .5, 64, "python:latest"),
        ]:
            with self.assertRaises(sandbox.SandboxDenied):
                sandbox.docker_args("bad", stage, image, mem, cpu, pids)


    def test_actual_container_nonzero_exit_never_passes(self):
        # Mock only the Docker control plane; Git archive and SQLite are real.
        sandbox.submit(self.conn, self.repo, self.commit, "python_unittest")
        cid = "f" * 64
        def fake_checked(args, cwd=None, timeout=12):
            if args[0] == "git":
                return str(self.repo) if args[-1] == "--show-toplevel" else "commit"
            if args[:2] == ["docker", "create"]:
                return cid
            if args[:2] == ["docker", "inspect"]:
                return json.dumps([{"State": {"ExitCode": 12, "OOMKilled": False}}])
            raise AssertionError(args)
        with patch.dict(os.environ, {"AEROFORGE_ENABLE_SANDBOX": "1"}), \
             patch.object(sandbox, "host_preflight", return_value={"mem_available_mib": 4096}), \
             patch.object(sandbox, "_run_checked", side_effect=fake_checked), \
             patch.object(sandbox, "verify_docker_config", return_value={"network_none": True}), \
             patch.object(sandbox, "_capture", return_value={"returncode": 0,
                         "output": "Tests failed", "timeout": False, "output_limit": False}), \
             patch.object(sandbox.subprocess, "run") as rm:
            rm.return_value.returncode = 0
            receipt = sandbox.run_next(self.conn, approve=True, image_id="sha256:" + "a" * 64)
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["container_exit_code"], 12)
        self.assertFalse(receipt["oom_killed"])
        self.assertEqual(sandbox.queue_status(self.conn)[0]["status"], "failed")
        self.assertFalse((self.state / "sandbox/job-000001/snapshot").exists())


if __name__ == "__main__":
    unittest.main()
