"""AeroForge ForgeRunner V0.3: explicitly approved, isolated Git snapshots.

Trusted controller only. Container code is untrusted. No host credentials, Docker
socket, network, writable source mounts, image pulling or production deployment.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import getpass
import hashlib
import io
import json
import os
import pathlib
import re
import selectors
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
import uuid

from .storage import home, now

PROFILES = {"python_unittest": ["python", "-B", "-m", "unittest", "discover", "-s", "tests", "-v"]}
MAX_ARCHIVE = 40 * 1024 * 1024
MAX_CONTENT = 25 * 1024 * 1024
MAX_FILES = 1000
MAX_LOG = 256 * 1024
IMAGE_PATTERN = re.compile(r"sha256:[a-f0-9]{64}\Z")
COMMIT_PATTERN = re.compile(r"[a-f0-9]{40}\Z")
DENY_PARTS = {".git", ".ssh", ".aws", ".kube", ".config", ".env", ".npmrc",
              ".pypirc", "credentials", "secrets", "id_rsa", "id_ed25519", ".docker"}
DENY_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".kdbx", ".sqlite", ".db")
REDACTIONS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{12,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{12,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"AIza[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?i)\b(token|password|api[_-]?key|secret)\s*[:=]\s*\S+"),
)


class SandboxDenied(RuntimeError):
    """Failure of a mandatory safety boundary."""


def _run_checked(args, cwd=None, timeout=12):
    p = subprocess.run(args, cwd=cwd, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C",
        "HOME": "/nonexistent"}, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=timeout, check=False)
    if p.returncode:
        raise SandboxDenied("Trusted preflight command failed: " + args[0])
    return p.stdout.decode("utf-8", errors="replace").strip()


def _allowed_repo(repo):
    roots = os.environ.get("AEROFORGE_SOURCE_ROOTS", "")
    allowed = [pathlib.Path(x).resolve() for x in roots.split(os.pathsep) if x]
    if not allowed:
        raise SandboxDenied("AEROFORGE_SOURCE_ROOTS is required")
    p = pathlib.Path(repo).expanduser().resolve(strict=True)
    if not p.is_dir() or not any(p == root or root in p.parents for root in allowed):
        raise SandboxDenied("Repository outside explicitly allowed source roots")
    actual = pathlib.Path(_run_checked(["git", "-C", str(p), "rev-parse", "--show-toplevel"]))
    if actual.resolve() != p:
        raise SandboxDenied("Require exact Git repository root")
    return p


def _commit(repo, sha):
    if not COMMIT_PATTERN.fullmatch(sha):
        raise SandboxDenied("Require full 40-character pinned commit SHA")
    found = _run_checked(["git", "-C", str(repo), "cat-file", "-t", sha])
    if found != "commit":
        raise SandboxDenied("Source revision is not a Git commit")
    return sha


def ensure_schema(con):
    con.execute("""CREATE TABLE IF NOT EXISTS sandbox_jobs(
      id INTEGER PRIMARY KEY AUTOINCREMENT, repo TEXT NOT NULL,
      commit_sha TEXT NOT NULL, profile TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'queued', requested_at TEXT NOT NULL,
      updated_at TEXT NOT NULL, receipt_path TEXT, outcome TEXT)""")
    con.commit()


def submit(con, repo, commit, profile):
    ensure_schema(con)
    if profile not in PROFILES:
        raise SandboxDenied("Unsupported execution profile")
    path = _allowed_repo(repo)
    _commit(path, commit)
    cur = con.execute("""INSERT INTO sandbox_jobs
      (repo,commit_sha,profile,status,requested_at,updated_at)
      VALUES(?,?,?,'queued',?,?)""", (str(path), commit, profile, now(), now()))
    con.commit()
    return cur.lastrowid


def _safe_archive_path(path):
    parts = pathlib.PurePosixPath(path).parts
    if not parts or path.startswith("/") or any(p in ("", ".", "..") for p in parts):
        raise SandboxDenied("Archive contains unsafe path")
    for part in parts:
        lowered = part.lower()
        if lowered in DENY_PARTS or lowered.startswith(".env.") and lowered != ".env.example":
            raise SandboxDenied("Archive contains prohibited secret-like filename")
        if lowered.endswith(DENY_SUFFIXES):
            raise SandboxDenied("Archive contains prohibited secret-like suffix")
    return pathlib.PurePosixPath(path)


def _stage_archive(repo, sha, dest):
    """Git archive, strict byte quota, reject links/devices and secret-like names."""
    dest.mkdir(mode=0o755, parents=True, exist_ok=False)
    proc = subprocess.Popen(["git", "-C", str(repo), "archive", "--format=tar", sha],
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LC_ALL": "C"},
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    content = bytearray()
    try:
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > MAX_ARCHIVE:
                proc.kill()
                raise SandboxDenied("Git snapshot exceeds archive size cap")
        if proc.wait(timeout=20) != 0:
            raise SandboxDenied("Git archive failed")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        if proc.stdout:
            proc.stdout.close()
    count, total = 0, 0
    manifest = []
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:") as tf:
        for member in tf:
            rel = _safe_archive_path(member.name)
            if member.isdir():
                continue
            if not member.isfile():
                raise SandboxDenied("Symlink, hardlink or special file forbidden in Git snapshot")
            count += 1
            total += member.size
            if count > MAX_FILES or total > MAX_CONTENT:
                raise SandboxDenied("Git snapshot exceeds file/size budget")
            target = dest.joinpath(*rel.parts)
            target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
            with os.fdopen(fd, "wb") as output:
                source = tf.extractfile(member)
                if source is None:
                    raise SandboxDenied("Missing Git snapshot file")
                remaining = member.size
                digest = hashlib.sha256()
                while remaining:
                    piece = source.read(min(65536, remaining))
                    if not piece:
                        raise SandboxDenied("Truncated Git snapshot file")
                    output.write(piece)
                    digest.update(piece)
                    remaining -= len(piece)
            os.chmod(target, 0o644)
            manifest.append({"path": str(rel), "sha256": digest.hexdigest(), "bytes": member.size})
    return {"files": count, "bytes": total, "manifest_sha256": hashlib.sha256(
        json.dumps(manifest, sort_keys=True).encode()).hexdigest()}


def host_preflight(memory_mib, swap_max_ratio=0.80):
    mem = {}
    for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            try:
                mem[k] = int(v.strip().split()[0]) // 1024
            except (ValueError, IndexError):
                pass
    avail = mem.get("MemAvailable", 0)
    swap_total = mem.get("SwapTotal", 0)
    swap_used = swap_total - mem.get("SwapFree", 0)
    ratio = round(swap_used / swap_total, 4) if swap_total else 0.0
    if avail < max(1024, memory_mib * 3):
        raise SandboxDenied("HostMemoryPressure: available RAM below safety margin")
    if swap_total and ratio > swap_max_ratio:
        raise SandboxDenied("HostMemoryPressure: swap usage above 80% threshold")
    return {"mem_available_mib": avail, "swap_used_mib": swap_used,
            "swap_total_mib": swap_total, "swap_ratio": ratio}


def docker_args(name, stage, image_id, memory_mib=256, cpus=0.5, pids=64):
    if not IMAGE_PATTERN.fullmatch(image_id):
        raise SandboxDenied("Container image must be pinned by local SHA-256 image ID")
    if not 128 <= memory_mib <= 512:
        raise SandboxDenied("Sandbox memory must be 128..512 MiB")
    if not 0.25 <= cpus <= 1.0:
        raise SandboxDenied("Sandbox CPU quota must be 0.25..1 CPU")
    if not 16 <= pids <= 96:
        raise SandboxDenied("Sandbox process budget must be 16..96")
    stage = pathlib.Path(stage).resolve(strict=True)
    if not stage.is_dir():
        raise SandboxDenied("Missing prepared source snapshot")
    return ["docker", "create", "--name", name,
        "--pull=never", "--network=none", "--read-only",
        "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
        "--ipc=private",
        "--user=65534:65534",
        "--memory=" + str(memory_mib) + "m",
        "--memory-swap=" + str(memory_mib) + "m",
        "--cpus=" + str(cpus), "--pids-limit=" + str(pids),
        "--ulimit=nofile=256:256",
        "--mount=type=bind,src=" + str(stage) + ",dst=/workspace,readonly",
        "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777",
        "--workdir=/workspace", "--env=HOME=/tmp",
        "--env=PYTHONDONTWRITEBYTECODE=1", "--env=PYTHONNOUSERSITE=1",
        "--log-driver=none", image_id] + PROFILES["python_unittest"]


def verify_docker_config(inspection, stage, memory_mib, cpus, pids):
    host = inspection.get("HostConfig", {})
    config = inspection.get("Config", {})
    checks = {
        "network_none": host.get("NetworkMode") == "none",
        "pid_namespace": host.get("PidMode") in ("", "private"),
        "uts_namespace": host.get("UTSMode") in ("", "private"),
        "read_only": host.get("ReadonlyRootfs") is True,
        "not_privileged": host.get("Privileged") is False,
        "memory_limit": host.get("Memory") == memory_mib * 1048576,
        "no_swap": host.get("MemorySwap") == memory_mib * 1048576,
        "cpu_limit": host.get("NanoCpus") == round(cpus * 1e9),
        "process_limit": host.get("PidsLimit") == pids,
        "no_caps": "ALL" in (host.get("CapDrop") or []),
        "no_new_privileges": any("no-new-privileges" in s for s in host.get("SecurityOpt") or []),
        "nonroot": config.get("User") == "65534:65534",
        "bind_only_source": len(host.get("Mounts") or []) == 1 and all(
            m.get("Source") == str(pathlib.Path(stage).resolve())
            and m.get("Target") == "/workspace" and m.get("ReadOnly") is True
            for m in host.get("Mounts") or []),
        "no_host_credentials_env": not any(re.search(
            r"(TOKEN|SECRET|PASSWORD|API_KEY|AWS_|GITHUB_|GH_TOKEN|SSH_AUTH_SOCK)", e, re.I)
            for e in config.get("Env") or []),
    }
    if not all(checks.values()):
        raise SandboxDenied("Docker isolation config verification failed: " +
                            ",".join(k for k, v in checks.items() if not v))
    return checks


def _capture(args, seconds):
    """Bounded console capture; prevents a job flooding trusted disk/memory."""
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LC_ALL": "C"},
        start_new_session=True)
    buf = bytearray()
    deadline = time.monotonic() + seconds
    selector = selectors.DefaultSelector()
    os.set_blocking(p.stdout.fileno(), False)
    selector.register(p.stdout, selectors.EVENT_READ)
    timed_out, limit_hit = False, False
    try:
        while True:
            if time.monotonic() >= deadline:
                timed_out = True
                break
            events = selector.select(timeout=0.2)
            for key, _ in events:
                data = os.read(key.fd, 8192)
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                if len(buf) + len(data) > MAX_LOG:
                    buf.extend(data[:max(0, MAX_LOG - len(buf))])
                    limit_hit = True
                    break
                buf.extend(data)
            if limit_hit:
                break
            if p.poll() is not None and not selector.get_map():
                break
        if timed_out or limit_hit:
            p.kill()
        try:
            p.wait(timeout=4)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(timeout=3)
    finally:
        selector.close()
        p.stdout.close()
    output = buf.decode("utf-8", errors="replace")
    for pat in REDACTIONS:
        output = pat.sub("[REDACTED]", output)
    return {"returncode": p.returncode, "output": output,
            "timeout": timed_out, "output_limit": limit_hit}


def run_next(con, approve=False, memory_mib=256, cpus=0.5, timeout_seconds=90, image_id=None):
    ensure_schema(con)
    if not approve or os.environ.get("AEROFORGE_ENABLE_SANDBOX") != "1":
        raise SandboxDenied("Explicit --approve and AEROFORGE_ENABLE_SANDBOX=1 required")
    if not 15 <= timeout_seconds <= 180:
        raise SandboxDenied("Timeout must be 15..180 seconds")
    # Reject invalid configuration before claiming any job.
    image_id = image_id or os.environ.get("AEROFORGE_RUNNER_IMAGE_ID", "")
    if not IMAGE_PATTERN.fullmatch(image_id):
        raise SandboxDenied("Pinned local runner image ID required")
    root = home() / "sandbox"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(root / "runner.lock", "a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SandboxDenied("Another ForgeRunner is already active")
        row = con.execute("""SELECT id,repo,commit_sha,profile FROM sandbox_jobs
            WHERE status='queued' ORDER BY id LIMIT 1""").fetchone()
        if row is None:
            return {"status": "empty"}
        job = dict(row)
        # Memory/swap probe first: blocked jobs stay queued for retry.
        preflight = host_preflight(memory_mib)
        repo = _allowed_repo(job["repo"])
        _commit(repo, job["commit_sha"])
        if job["profile"] not in PROFILES:
            raise SandboxDenied("Unsupported profile in queue")
        run = root / ("job-" + str(job["id"]).zfill(6))
        run.mkdir(mode=0o700, exist_ok=False)
        con.execute("UPDATE sandbox_jobs SET status='running',updated_at=? WHERE id=?",
                    (now(), job["id"]))
        con.commit()
        name = "aeroforge-" + uuid.uuid4().hex[:16]
        cid = None
        stage = run / "snapshot"
        try:
            evidence = _stage_archive(repo, job["commit_sha"], stage)
            args = docker_args(name, stage, image_id, memory_mib, cpus)
            cid = _run_checked(args, timeout=20).splitlines()[-1]
            if not re.fullmatch(r"[a-f0-9]{64}", cid):
                raise SandboxDenied("Docker returned invalid container ID")
            inspected = json.loads(_run_checked(["docker", "inspect", cid], timeout=10))
            checks = verify_docker_config(inspected[0], stage, memory_mib, cpus, 64)
            result = _capture(["docker", "start", "-a", cid], timeout_seconds + 8)
            # Docker CLI's status is NOT the test process status. Inspect the
            # actual container exit code and OOM outcome before marking pass.
            after = json.loads(_run_checked(["docker", "inspect", cid], timeout=10))[0]
            state = after.get("State", {})
            container_exit = state.get("ExitCode")
            oom = state.get("OOMKilled") is True
            pass_status = (result["returncode"] == 0 and container_exit == 0
                           and not oom and not result["timeout"] and not result["output_limit"])
            # Never certify success while the container or source snapshot remains.
            removed = subprocess.run(["docker", "rm", "-f", cid],
                capture_output=True, timeout=15,
                env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"})
            if removed.returncode != 0:
                raise SandboxDenied("Container cleanup failed")
            cid = None
            shutil.rmtree(stage)
            if stage.exists():
                raise SandboxDenied("Source snapshot cleanup failed")
            status = "passed" if pass_status else "failed"
            log = run / "console.log"
            log.write_text(result["output"] + "\n", encoding="utf-8")
            os.chmod(log, 0o600)
            receipt = {"schema": "aeroforge.sandbox.v1", "job_id": job["id"],
                "repo": job["repo"], "commit_sha": job["commit_sha"], "profile": job["profile"],
                "image_id": image_id, "status": status, "approved_by_cli_user": getpass.getuser(),
                "created_at": now(), "limits": {"memory_mib": memory_mib, "cpus": cpus,
                "pids": 64, "timeout_seconds": timeout_seconds, "network": "none"},
                "resource_preflight": preflight, "isolation_checks": checks,
                "snapshot": evidence,
                "console_sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
                "docker_client_exit": result["returncode"], "container_exit_code": container_exit,
                "oom_killed": oom, "timeout": result["timeout"],
                "output_limit": result["output_limit"],
                "production_deployed": False, "github_check_published": False}
            rp = run / "receipt.json"
            rp.write_text(json.dumps(receipt, indent=2) + "\n")
            os.chmod(rp, 0o600)
            con.execute("""UPDATE sandbox_jobs SET status=?,updated_at=?,receipt_path=?,outcome=?
                 WHERE id=?""", (status, now(), str(rp), "Container sandbox completed", job["id"]))
            con.commit()
            return receipt
        except Exception as exc:
            summary = type(exc).__name__ + ": " + (
                str(exc) if isinstance(exc, SandboxDenied) else "sandbox internal failure")
            con.execute("""UPDATE sandbox_jobs SET status='blocked',updated_at=?,outcome=?
                 WHERE id=?""", (now(), summary[:220], job["id"]))
            con.commit()
            return {"job_id": job["id"], "status": "blocked", "reason": summary[:220]}
        finally:
            if cid:
                subprocess.run(["docker", "rm", "-f", cid], capture_output=True, timeout=12,
                    env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"})
            if stage.exists():
                shutil.rmtree(stage)
            fcntl.flock(lock, fcntl.LOCK_UN)


def queue_status(con):
    ensure_schema(con)
    return [dict(r) for r in con.execute("""SELECT id,repo,commit_sha,profile,status,
        requested_at,updated_at,receipt_path,outcome FROM sandbox_jobs
        ORDER BY id DESC LIMIT 30""")]
