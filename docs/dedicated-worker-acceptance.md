# AeroForge V0.3.2 — dedicated ForgeRunner acceptance

**Scope:** a standalone, explicitly approved, non-production worker **with rootless Docker**, not NXCore. This is an acceptance tool for a harmless in-container probe, *not* a GitHub Actions self-hosted runner, production deployment, or license/billing bypass.

## Before execution

- Use a dedicated VM or host that has no mounted production shares, no AVCC/ACOS credentials, and no GitHub personal access tokens. Do not run on NXCore.
- Rootless Docker daemon must be available on the invoking user's standard socket `unix:///run/user/$(id -u)/docker.sock`. The probe deliberately ignores any inherited `DOCKER_HOST` and refuses a rootful Docker daemon.
- Preinstall the desired Python 3 image on the worker through an independently approved process. The acceptance tool **never pulls** an image. Obtain the pinned ID: `docker image inspect python:3.12-alpine --format '{{.Id}}'`.
- At least 1 GiB of host available memory and no more than 80% swap used. Do not loosen this threshold to force acceptance.

## Commands

```bash
# Safe preflight only; does not create or execute containers
python3 scripts/forge_runner_acceptance.py \
  --image-id "$(docker image inspect python:3.12-alpine --format '{{.Id}}')"

# On the approved dedicated worker, after inspecting the source and accepting risks:
python3 scripts/forge_runner_acceptance.py \
  --image-id "$(docker image inspect python:3.12-alpine --format '{{.Id}}')" \
  --approve-runtime \
  --output ./forge-runner-acceptance.json

# Unit tests (mocked Docker control plane; safe anywhere)
python3 -m unittest discover -s tests -p 'test_dedicated_worker_acceptance.py' -v
```

## What the probe verifies

- Full locally installed SHA-256 image ID; rootless Docker daemon; no attempt to read or use a production GitHub token.
- Single disposable container configured with `--network=none`, `--read-only`, nonroot UID, no capabilities, no-new-privileges, no mounts, no container swap, 128 MiB RAM, 0.25 CPU, 32 processes, and a small temporary filesystem.
- Actual `docker inspect` policy values **before** starting the container.
- In-container external IP connection fails, production host paths and Docker socket are absent, root filesystem refuses writes, and cgroup memory/CPU/PID ceilings match the policy.
- Container exit code and OOM status checked after completion; container forcibly removed in `finally` even after an error. Successful response is withheld if removal fails.
- Local JSON receipt written with mode 0600. The probe runs only a fixed inline Python standard-library script, never a checked-out repository's code.

## What is **not** verified by a pass

This is a safe basic boundary smoke, **not an adversarial isolation certificate**. The tool does not inject OOM or PID exhaustion, test malicious container escape, perform an extended soak, test kill-switch cancellation or verify GitHub Checks delivery. Those remain independent acceptance gates. Rootless Docker reduces privileges but does not substitute for a dedicated VM/security review. It does **not** register a GitHub runner or make required checks green.

## Verified in this development pass

The tool's Python unit tests passed, including rejection of NXCore, excessive swap, rootful daemon, unsafe Docker policy, unsafe runtime probe results, and missing approval, plus mocked successful and failing container lifecycle with cleanup. **No real dedicated worker was connected**, so the actual Docker runtime smoke is **not yet performed**. Keep V0.3 ForgeRunner disabled until this and the remaining security tests pass on an approved host.
