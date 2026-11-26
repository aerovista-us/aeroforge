# ForgeRunner V0.3 — isolated test execution

**Status:** Development preview. The default AeroForge worker remains static/read-only. ForgeRunner's container execution is **disabled unless explicitly approved and enabled**. This milestone does not deploy any service, publish GitHub statuses, merge PRs, or perform production changes.

## Boundary and architecture

AeroForge's trusted Python controller queues an immutable Git commit. At execution time it obtains only the pinned Git tree via `git archive`, rejects links, secret-like file paths and oversized sources, copies ordinary files to a temporary private scratch directory, and binds that *snapshot only* read-only into a disposable container.

The untrusted test code runs without GitHub credentials, host environment variables, production filesystem mounts or host networking. Git history, SSH keys, Docker socket, ACOS services, AVCC data, NXCore home and production credentials are not mounted.

The controller (which does have access to the host Docker daemon) must remain trusted and protected. A normal Docker daemon is **not** a security boundary equivalent to a dedicated VM: if adversarial code is in scope, prefer a separate worker machine/VM with a rootless daemon and no access to production storage. This implementation prevents intentional credential pass-through; it cannot guarantee safety against kernel/container-runtime vulnerabilities or secrets mistakenly committed into ordinary source files.

## Enforced Docker policy

- Image: preinstalled **full local image ID** `sha256:<64-hex>`, no pull.
- Network: `none`, no published ports or external services.
- Filesystem: read-only image plus only one read-only source-snapshot bind; `/tmp` is a 64 MiB noexec/nosuid/nodev tmpfs.
- Credentials: subprocess environment replaced with minimal safe variables; no token or SSH environment is passed into Docker; source archive rejects common secret-like file names.
- User: UID/GID `65534:65534`, all Linux capabilities dropped; `no-new-privileges`.
- Limits: default **256 MiB memory with zero container swap**, **0.5 CPU**, **64 PIDs**, **90 seconds** wall clock. Allowed ranges: memory 128–512 MiB, CPU 0.25–1.0 CPU, processes 16–96, timeout 15–180 seconds.
- Preflight: at least max(1 GiB, 3×requested container memory) available; if host swap is over **80% used**, deny execution. Because NXCore currently has high swap use, this intentionally blocks sandbox starts there.
- Concurrency: one ForgeRunner process via an exclusive `flock` file.
- Verification: `docker create` followed by inspecting the **actual** container configuration and rejecting any missing controls before `docker start -a`.
- Exit status: verify the **container process exit code**, OOM-killed flag, Docker client return, timeout and maximum log bytes; failing any means not passed.
- Evidence: private SHA-256 receipt, source snapshot manifest hash, redacted capped console log, actual image ID and enforced budgets. Snapshot is deleted after the run.

## Commands

Do not set the enable flag on production until the security acceptance is complete.

```bash
cd /srv/ACOS/_agent_worktrees/aeroforge-v03-20261125
export AEROFORGE_STATE=/srv/AV-MEMORY/aeroforge
export AEROFORGE_SOURCE_ROOTS=/srv/ACOS/_agent_worktrees/aeroforge-v03-20261125
python3 -m aeroforge sandbox-status
python3 -m aeroforge sandbox-submit --repo "$AEROFORGE_SOURCE_ROOTS" \
  --commit "$(git rev-parse HEAD)" --profile python_unittest

# On an approved, isolated worker with healthy resource headroom:
export AEROFORGE_ENABLE_SANDBOX=1
export AEROFORGE_RUNNER_IMAGE_ID="$(docker image inspect python:3.12-alpine --format '{{.Id}}')"
python3 -m aeroforge sandbox-run-next --approve --memory-mib 256 --cpus 0.5 \
  --timeout-seconds 90
```

Only `python_unittest` is supported at V0.3. No shell scripts, arbitrary commands, package downloads or dependencies are accepted. An unavailable/missing Python dependency causes the job to fail; AeroForge does not attempt to install it. No auto-run daemon or webhook is installed.

## Acceptance evidence

- Existing V0.2.1 suites and dedicated ForgeRunner boundary tests must pass.
- A **stopped** Docker container created with exactly these flags has been inspected on NXCore; all 14 container configuration checks passed. It was removed without starting.
- A full untrusted-code runtime smoke is **not yet verified on NXCore**, because its current swap pressure trips the fail-closed preflight.
- The dedicated worker must prove inaccessible host secret paths, rejected egress, observed cgroup CPU/memory values, PID limit, timeout and OOM blocking before any unattended execution is allowed.

## Rollback

All changes are in the new `feat/forgerunner-sandbox-v0.3` Git branch. No existing runtime services or existing V0.2 queues are replaced. To stop using it, leave `AEROFORGE_ENABLE_SANDBOX` unset. Staged jobs remain stored as inert records and can be inspected with `sandbox-status`.
