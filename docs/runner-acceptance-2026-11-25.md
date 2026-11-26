# ForgeRunner V0.3 — verified acceptance record

**Host:** nxcore (Ubuntu 24.04.x)
**Assessment:** 2026-11-25 local / 2026-11-26 UTC
**Status:** Controller and stopped-container policy verified; runtime execution **blocked** by NXCore resource guard.

## Verified

- AeroForge Python automated suite: **45 passing tests**.
- Full 40-character Git commit requirement, Git root allowlist and explicit approval/enable controls.
- Source snapshot built from Git history (not mutable working tree). Secret-like tracked filenames, symlinks, hardlinks, special files and path traversal fail closed.
- Real `docker create` and `docker inspect` performed on the local pinned Python image without starting the container. **14 isolation checks passed**: no network, private PID/UTS, read-only root, unprivileged mode, exact RAM ceiling, no swap, CPU quota, PID limit, dropped capabilities, no-new-privileges, non-root UID, one read-only source bind and no credential-bearing environment.
- The Docker image ID was pinned as `sha256:979e5a8a56846771598d47ebf1f8a75b46155104e168a0b237c0783c1dedff8a`; it was not pulled.
- A development acceptance job was queued with a pinned Git SHA. Running it with explicit approval and enable was denied by `HostMemoryPressure: swap usage above 80% threshold`. The job **remained queued**, and no sandbox container was started.
- Controller tests distinguish the container's exit code/OOM state from the Docker client return code. Container and source snapshot cleanup are required for success.
- Test and policy evidence saved privately to:
  - `/srv/AV-MEMORY/aeroforge/reports/sandbox-policy-2026-11-25.json`
  - `/srv/AV-MEMORY/aeroforge/runner-acceptance/aeroforge.sqlite`

## Outstanding (no success claim)

- **Untrusted code execution smoke** on an approved worker with healthy memory and swap headroom.
- Runtime tests proving forbidden network egress and host credential/path access, cgroup quota behavior, PID exhaustion handling, OOM killing, timeout, output cap and cleanup after forced termination.
- Independent security review of the trusted controller, Docker/rootless-host trust boundary and image integrity.
- Authenticated GitHub Checks status publication, runner registration and AVCC/VCliq release gate integration.
- Production deployment, automatic scheduling or unattended execution.

**Recommendation:** Run the full dynamic acceptance on a dedicated worker/VM using a rootless container engine. Do not lower the swap threshold or enable unattended execution on NXCore to force the gate to pass.
