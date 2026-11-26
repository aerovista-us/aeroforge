# AeroForge
**Build. Verify. Deliver.** An AeroVista product powered by Nexus TechWorks.

AeroForge V0.3 (stacked on V0.2.1) is a working, resource-conscious foundation for GitHub workflow intelligence and local static validation. The project deliberately includes **no active `.github/workflows/` automation**, preserving the goal of **zero additional GitHub-hosted runner minutes** while the Free plan allowance is exhausted.

## What works
- **ForgeBudget + ForgeBridge (read-only):** Scan GitHub repositories and workflow YAML via GET requests only; record workflow triggers and actionable cost-optimization candidates. GitHub API rate limits apply. The scanner does not start or re-run jobs.
- **ForgeCore + ForgeRunner (safe lane):** Persistent SQLite queue with one worker lock; AST parsing, JSON parsing, GitHub workflow analysis, and file inventory. **No arbitrary shell or repository code execution in the default static lane.** Explicitly approved container-based unittest execution is separate and disabled by default.
- **ForgeGuard + ForgeLedger:** Path allowlists, symlink exclusion, size and wall-clock caps, SHA-256 evidence receipts, and durable status history.
- **ForgeDeck:** Self-contained HTML dashboard that works offline and is safe to open over the ACOS file share.

## V0.2: historical Actions usage observation

A separate read-only usage scanner retrieves a bounded sample of Actions workflow runs and their job start/end times. It adds a ranked *observed job-walltime* table to ForgeDeck, with explicit scan coverage, request caps, SHA-256 reports and safe resume slices. It never calls a workflow dispatch or rerun endpoint.

```bash
python3 -m aeroforge scan-usage --owner aerovista-us --days 30 --max-repos 250 --max-runs-per-repo 6 --max-requests 1500
python3 -m aeroforge usage-report
python3 -m aeroforge dashboard
```

Read [docs/usage.md](docs/usage.md) before live use. **Runtime ≠ GitHub-billed minutes.** We do not report unverified billable minutes or savings. Historical scans remain partial when capped or permission-restricted. An offline synthetic example is available via `import-usage-fixture`.

## Quick start on your own machine

```bash
cd AeroForge
export AEROFORGE_STATE="$PWD/.local-state" # choose a local ext4/NTFS volume, not shared exFAT
export AEROFORGE_ALLOWED_ROOTS="$PWD"
python3 -m aeroforge init
python3 -m aeroforge import-fixture examples/fixture.json
python3 -m aeroforge enqueue --kind python_syntax --target .
python3 -m aeroforge run-next
python3 -m aeroforge dashboard
python3 -m unittest discover -s tests -v
```

Open `.local-state/dashboard.html`. The demo fixture uses `example/*` repositories, **not** actual AeroVista account data.

## NXCore target

Windows share: `\\100.115.9.61\acos\AeroForge`  
Linux source: `/srv/ACOS/AeroForge`  
Persistent SQLite, reports and evidence: `/srv/AV-MEMORY/aeroforge`

After NXCore returns online, clone the repo at that Linux path as the normal unprivileged user (never overwrite an existing directory). Then:

```bash
cd /srv/ACOS/AeroForge
bash run-nxcore.sh init
bash run-nxcore.sh enqueue --kind python_syntax --target /srv/ACOS/AeroForge
bash run-nxcore.sh run-next
bash run-nxcore.sh dashboard
```

No always-on daemon is installed; invoke the worker explicitly. The host's available memory and swap must be reviewed before adding heavier execution lanes. No `sudo`, Docker socket or root permissions are needed for V0.1.

## GitHub inventory — read-only

With `GH_TOKEN` set to a token scoped to repository contents and metadata (read-only), run:

```bash
python3 -m aeroforge scan-github --owner aerovista-us --max-repos 250
```

The scanner uses the GitHub REST API to fetch repo metadata, workflow metadata and their YAML. Without a token it can scan **public repositories only** and marks the result partial. It does not use GitHub Actions minutes; however, it consumes GitHub API rate limits. It does not estimate runner-minute savings without historical run duration data.

A missing token/insufficient scope, rate limit, repository-limit truncation, or read failure must not be mistaken for a clean audit. The generated dashboard shows a partial/coverage notice.

## Workflow audit guidance

Warnings are **review candidates**, not automatic edits. For `push` + `pull_request` overlap, determine actual branch patterns before removing triggers. For concurrency, avoid cancelling deployments or evidence-critical jobs. For schedules, protect mandatory security and backup workflows. For jobs without timeouts, select a validated job-specific limit. **Never bulk-disable security/release pipelines.**

## Security and operating boundaries

- Treat repositories and workflow files as untrusted input. The system analyzes text; it does not execute workflow code.
- Explicit `AEROFORGE_ALLOWED_ROOTS` required for local checks; resolved targets must fall under these roots.
- Local worker holds a non-blocking exclusive lock and performs one bounded job at a time.
- Skips symlinks and oversized files; does not scan `.env` files.
- No production deployment, task ownership changes, GitHub writeback, self-hosted runner installation, or cross-account identity bridge is enabled.
- Store GitHub tokens only in environment variables or secret storage. Do not commit `.env` or SQLite state. Use least-privilege credentials.
- Version 0.1 static checks do **not** enforce CPU or RAM cgroups because they do not execute untrusted code. Container-based sandboxed test execution is a separate gated milestone.

## Roadmap

1. **V0.2:** Add read-only run/job observation and explicit partial-coverage reporting (implemented). Full authorized AeroVista scan and verified billing reconciliation remain open.
2. **V0.3:** Docker-isolated approved unittest runner with enforced CPU/RAM/PID/time caps, pinned local image and commit, private state and evidence receipts (implemented preview). NXCore runtime smoke remains blocked by swap safety threshold; see [docs/sandbox.md](docs/sandbox.md).
3. **V0.4:** ForgeBridge to VCliq + AVCC Release Board with authenticated read-only discovery and evidence reconciliation.
4. **V1:** ForgeDeck multiuser dashboard, authorized scoped execution, release gating and optional local AI resource planning.

See [docs/architecture.md](docs/architecture.md) for interfaces and controls.

### V0.2.1 historical evidence improvements

- Captures validated commit SHAs, branch names and run attempt from GitHub run metadata.
- Flags observed same-SHA push/PR pairs and runs returning no jobs without claiming billed-minute savings or a billing diagnosis.
- Performs in-place non-destructive migration of V0.2 SQLite usage history.
- Shows the new evidence categories on the offline ForgeDeck dashboard.

## Live AeroVista baseline (2026-11-25)

An authenticated GET-only scan on NXCore observed 202 repositories, 80 workflow definitions and 147 advisory findings. Workflow inventory has 41 retrieval errors and is **partial**. Historical 30-day sampled data includes 120 runs, 189 jobs and 60.5 observed job wall-minutes (not GitHub-billed usage). See [docs/baseline-2026-11-25.md](docs/baseline-2026-11-25.md) and private evidence on AV-MEMORY. No workflows were triggered by these scans.

## ForgeRunner V0.3

ForgeRunner adds an inert SQLite sandbox queue and explicit, approved Python unittest execution in a no-network, read-only, non-root Docker container with hard memory/CPU/PID limits. The trusted control plane uses a full pinned Git commit and local image ID, rejects secret-like paths and symlinks, verifies the real Docker configuration before launch and inspects actual container exit status. It does **not** mount host credentials or the Docker socket inside the container. It is disabled by default; see [docs/sandbox.md](docs/sandbox.md). The trusted Docker daemon itself remains a root-level host privilege; run untrusted code on dedicated worker infrastructure for stronger isolation.

### V0.3.1 — account-wide diagnostics and prioritization

A fresh authenticated inventory distinguishes workflow YAML HTTP 404s, workflow metadata authorization errors, and source parser failures in a durable per-scan table. The separately verified November 26, 2026 audit classified 41 stale workflow-file references as files absent from default-branch Git trees. Across 51 private repositories, 749 workflow run records were observed in 30 days, 647 (86.4%) from ACOS. **Run counts are not billable minutes.** See [docs/actions-baseline-2026-11-26.md](docs/actions-baseline-2026-11-26.md). No workflows were disabled or changed automatically.
