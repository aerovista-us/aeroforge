"""AeroForge V0.3.2 dedicated-worker runtime acceptance.

Self-contained, standard-library-only safety probe. Does NOT run repository code,
read GitHub tokens, register runners, or access the host's production services.
Requires --approve-runtime on an isolated machine with ROOTLESS Docker.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import socket
import subprocess
import sys
import uuid

MEMORY_MIB = 128
CPUS = 0.25
PIDS = 32
WALL_TIMEOUT = 25
IMAGE_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
CID_RE = re.compile(r"[0-9a-f]{64}\Z")

# Executed ONLY inside the disposable, non-networked container; no source bind.
PROBE = '''import json,os,pathlib,socket
blocked=[]
inaccessible=[]
for p in ("/srv/ACOS","/srv/AV-MEMORY","/root/.ssh","/var/run/docker.sock"):
 try:
  if pathlib.Path(p).exists(): blocked.append(p)
 except PermissionError:
  # Cannot even stat the path as a non-root process => inaccessible.
  inaccessible.append(p)
external_denied=False
try:
 sock=socket.create_connection(("1.1.1.1",443),timeout=2)
 sock.close()
except OSError:
 external_denied=True
root_read_only=False
try:
 with open("/etc/aeroforge_probe_write", "w") as f: f.write("bad")
except OSError:
 root_read_only=True
limits={}
for n in ("memory.max","cpu.max","pids.max"):
 p=pathlib.Path("/sys/fs/cgroup")/n
 limits[n]=p.read_text().strip() if p.exists() else None
print(json.dumps({"uid":os.getuid(),"gid":os.getgid(),"external_network_denied":external_denied,
 "root_filesystem_readonly":root_read_only,"unexpected_host_paths":blocked,
 "inaccessible_host_paths":inaccessible,"cgroup":limits}))
'''


class AcceptanceDenied(RuntimeError):
    pass


def read_meminfo(path="/proc/meminfo"):
    data = {}
    for line in pathlib.Path(path).read_text().splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            try:
                data[key] = int(value.strip().split()[0]) // 1024
            except (ValueError, IndexError):
                continue
    return data


def check_host(hostname=None, meminfo=None):
    host = (hostname if hostname is not None else socket.gethostname()).strip().lower()
    if host == "nxcore" or host.startswith("nxcore."):
        raise AcceptanceDenied("Production NXCore is not an authorized worker")
    d = meminfo if meminfo is not None else read_meminfo()
    available = d.get("MemAvailable", 0)
    swap_total = d.get("SwapTotal", 0)
    swap_used = max(0, swap_total - d.get("SwapFree", 0))
    swap_ratio = swap_used / swap_total if swap_total else 0
    if available < 1024 or swap_ratio > 0.8:
        raise AcceptanceDenied("Insufficient memory headroom or swap usage over 80%")
    return {"hostname": host, "available_mib": available, "swap_ratio": round(swap_ratio, 3)}


def execute(args, timeout=12):
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/nonexistent", "LC_ALL": "C",
           "DOCKER_HOST": f"unix:///run/user/{os.getuid()}/docker.sock"}
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)


def rootless_docker(info):
    # Docker daemon security options for RootlessKit include 'name=rootless'.
    return any("rootless" in str(item).lower() for item in info.get("SecurityOptions", []))


def docker_preflight(image):
    if not IMAGE_RE.fullmatch(image):
        raise AcceptanceDenied("Require an exact installed local Docker image sha256 ID")
    proc = execute(["docker", "info", "--format", "{{json .}}"])
    if proc.returncode:
        raise AcceptanceDenied("Unable to inspect Docker daemon")
    try:
        info = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise AcceptanceDenied("Invalid Docker daemon information") from None
    if not rootless_docker(info):
        raise AcceptanceDenied("Requires rootless Docker on a dedicated worker")
    proc = execute(["docker", "image", "inspect", image, "--format", "{{.Id}}"])
    if proc.returncode or proc.stdout.strip() != image:
        raise AcceptanceDenied("Pinned image is not installed locally")
    return {"rootless_docker": True, "image": image}


def build_command(image, name):
    if not IMAGE_RE.fullmatch(image):
        raise AcceptanceDenied("Unpinned image")
    return ["docker", "create", "--name", name, "--pull=never",
            "--network=none", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true", "--ipc=private",
            "--user=65534:65534", "--memory=128m", "--memory-swap=128m",
            "--cpus=0.25", "--pids-limit=32", "--ulimit=nofile=128:128",
            "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777",
            "--log-driver=none", "--env=HOME=/tmp", image,
            "python", "-B", "-c", PROBE]


def verify_container(inspection):
    host = inspection.get("HostConfig", {})
    config = inspection.get("Config", {})
    checks = {
        "no_network": host.get("NetworkMode") == "none",
        "read_only_root": host.get("ReadonlyRootfs") is True,
        "no_privilege": host.get("Privileged") is False,
        "all_caps_dropped": "ALL" in (host.get("CapDrop") or []),
        "no_new_privileges": any("no-new-privileges" in opt for opt in host.get("SecurityOpt") or []),
        "memory_limit": host.get("Memory") == MEMORY_MIB * 1024 * 1024,
        "swap_disabled": host.get("MemorySwap") == MEMORY_MIB * 1024 * 1024,
        "cpu_limit": host.get("NanoCpus") == round(CPUS * 1_000_000_000),
        "pids_limit": host.get("PidsLimit") == PIDS,
        "non_root_user": config.get("User") == "65534:65534",
        "no_host_mounts": not bool(host.get("Binds") or host.get("Mounts")),
        "pid_isolated": host.get("PidMode") in ("", "private"),
        "uts_isolated": host.get("UTSMode") in ("", "private"),
        "no_credential_env": all(not any(word in x.split("=", 1)[0].upper() for word in
                                  ("TOKEN", "SECRET", "PASSWORD", "AWS_", "SSH_", "GITHUB_", "API_KEY"))
                                 for x in config.get("Env") or []),
    }
    if not all(checks.values()):
        raise AcceptanceDenied("Container configuration failed: " + ", ".join(k for k,v in checks.items() if not v))
    return checks


def verify_probe(probe):
    limits = probe.get("cgroup", {})
    try:
        cpu_quota, cpu_period = (int(x) for x in limits["cpu.max"].split())
    except (ValueError, TypeError, KeyError, AttributeError):
        raise AcceptanceDenied("CPU cgroup verification unavailable") from None
    if cpu_quota / cpu_period > CPUS or cpu_period <= 0:
        raise AcceptanceDenied("CPU quota exceeds policy")
    try:
        memory = int(limits["memory.max"])
        pids = int(limits["pids.max"])
    except (ValueError, TypeError, KeyError):
        raise AcceptanceDenied("Memory/PID cgroup verification unavailable") from None
    checks = {"nonroot_inside": probe.get("uid") == 65534,
              "network_denied_inside": probe.get("external_network_denied") is True,
              "rootfs_readonly_inside": probe.get("root_filesystem_readonly") is True,
              "no_host_paths_inside": probe.get("unexpected_host_paths") == [],
              "memory_ceiling_inside": 0 < memory <= MEMORY_MIB * 1024 * 1024,
              "cpu_ceiling_inside": True,
              "pid_ceiling_inside": 0 < pids <= PIDS}
    if not all(checks.values()):
        raise AcceptanceDenied("Container runtime acceptance failed: " + ", ".join(k for k,v in checks.items() if not v))
    return checks


def run_runtime(image, host):
    name = "aeroforge-acceptance-" + uuid.uuid4().hex[:12]
    cid = None
    cleanup = False
    try:
        created = execute(build_command(image, name), timeout=20)
        if created.returncode:
            raise AcceptanceDenied("Docker create failed")
        cid = created.stdout.strip()
        if not CID_RE.fullmatch(cid):
            raise AcceptanceDenied("Docker create returned invalid ID")
        response = execute(["docker", "inspect", cid])
        if response.returncode:
            raise AcceptanceDenied("Docker inspect failed")
        inspected = json.loads(response.stdout)[0]
        static_checks = verify_container(inspected)
        started = execute(["docker", "start", "-a", cid], timeout=WALL_TIMEOUT)
        if started.returncode:
            # Fixed probe has no credentials; include only a bounded diagnostic.
            # Never relax the resource policy to obtain a green check.
            detail = " ".join((getattr(started, "stderr", None) or getattr(started, "stdout", None) or "").strip().split())[:240]
            raise AcceptanceDenied("Docker start failed (exit %s): %s" % (started.returncode, detail))
        response = execute(["docker", "inspect", cid])
        if response.returncode:
            raise AcceptanceDenied("Post-execution Docker inspect failed")
        state = json.loads(response.stdout)[0]["State"]
        if state.get("ExitCode") != 0 or state.get("OOMKilled") is True:
            raise AcceptanceDenied("Container exited unsuccessfully or OOM killed")
        try:
            lines = [line.strip() for line in started.stdout.splitlines() if line.strip()]
            probe = json.loads(lines[-1])
        except (ValueError, IndexError):
            raise AcceptanceDenied("No valid runtime proof") from None
        runtime_checks = verify_probe(probe)
        return {"accepted": True, "host": host, "docker_checks": static_checks,
                "runtime_checks": runtime_checks, "image_id": image,
                "process_exit": state["ExitCode"],
                "captured_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                "not_tested": ["OOM fault injection", "forced timeout cleanup", "adversarial container escape resistance",
                               "GitHub self-hosted runner registration", "production release gating"]}
    finally:
        if cid:
            removed = execute(["docker", "rm", "-f", cid], timeout=15)
            cleanup = removed.returncode == 0
            if not cleanup:
                raise AcceptanceDenied("Cleanup failed; container must be removed manually")


def main(argv=None):
    parser = argparse.ArgumentParser(description="AeroForge isolated-worker acceptance (not for NXCore)")
    parser.add_argument("--image-id", required=True, help="Already installed full sha256 image ID")
    parser.add_argument("--approve-runtime", action="store_true", help="Explicitly create/start disposable container")
    parser.add_argument("--output", default="aeroforge-worker-acceptance.json")
    opts = parser.parse_args(argv)
    try:
        host = check_host()
        docker = docker_preflight(opts.image_id)
        if not opts.approve_runtime:
            report = {"accepted": False, "status": "preflight_only", "host": host, "docker": docker,
                      "runtime_executed": False}
        else:
            report = run_runtime(opts.image_id, host)
        target = pathlib.Path(opts.output).expanduser()
        target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if os.name == "posix":
            target.chmod(0o600)
        print(json.dumps(report, indent=2))
        return 0 if (report.get("accepted") or report.get("status") == "preflight_only") else 2
    except (AcceptanceDenied, subprocess.TimeoutExpired, OSError, KeyError, json.JSONDecodeError) as error:
        print("AeroForge acceptance blocked:", type(error).__name__, str(error)[:160], file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
