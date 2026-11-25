# AeroForge V0.2 — GitHub Actions usage observation

**Scope:** GET-only account-owned repository/run/job history. No workflows are triggered and no repository settings are changed. It is **not** a billing ledger.

## Real data (authorized token required for private repos)

```bash
cd /srv/ACOS/AeroForge
export AEROFORGE_STATE=/srv/AV-MEMORY/aeroforge
# Provide a READ-ONLY token through your authorized secret store, never git or terminal history:
# GH_TOKEN is provided to the process environment at runtime.
python3 -m aeroforge scan-github --owner aerovista-us --max-repos 250
python3 -m aeroforge scan-usage --owner aerovista-us --days 30 \
  --max-repos 250 --max-runs-per-repo 6 --max-requests 1500
python3 -m aeroforge usage-report
python3 -m aeroforge dashboard
```

The first command discovers workflow definitions and low-risk review candidates; the second observes a *capped sample* of historical runs and job times. The maximum API budget is explicitly capped; the report states when coverage is partial and why. Even with a large cap, `max_runs_per_repo` and pagination can leave data uncollected. Check the report's coverage and account permission before drawing conclusions.

To scan later alphabetical repositories after a capped run, use `--start-after aerovista-us/previous-repository`. This is **another partial scan**, not automatically merged into a full audit. Scan reports and hashes live in `state/reports/usage-NNNNNN.json` (or `AEROFORGE_STATE/reports/`).

### What the job runtime means

`observed_job_wall_minutes` adds `(completed_at - started_at)` for job records in the collected sample. This is **not** GitHub-billed minutes. GitHub billing may round up individual jobs, multiply by OS type, exclude eligible self-hosted jobs, or depend on plan and policy. We deliberately don't infer savings in dollars or billable minutes from the observed figure. Nor do we claim that a run marked `failure` necessarily executed: jobs may be empty because GitHub declined to start the runner.

`runner_kind=unknown` is used unless a self-hosted label is explicitly present. We don't assume `GitHub Actions` names prove provider type. Samples may also be missing runs, attempts, queued jobs, workflows not in the account scope, and incomplete job pages; those limitations must remain visible.

### Ranking and next action

ForgeDeck orders observed workflows by sampled job wall-time and shows the number of static-review findings matching each workflow. Review high-runtime jobs with overlap, missing timeouts, and broad triggers first; **do not automatically disable** required security, production, backup or release checks. Make one workflow PR at a time, confirm expected triggers and protected branch rules, and measure actual historical usage after billing/runners are available again.

### Offline example

```bash
export AEROFORGE_STATE="$PWD/.demo-state"
python3 -m aeroforge import-fixture examples/fixture.json
python3 -m aeroforge import-usage-fixture examples/usage_fixture.json
python3 -m aeroforge dashboard
```

The offline fixture is synthetic and the resulting report is explicitly marked **partial / fixture**, never real GitHub usage. It is suitable for testing the UI while GitHub-hosted Actions are unavailable.

## Security and authorization

- Use read-only Contents/Metadata and Actions permissions for the authorized repository set. If an endpoint cannot be read, record partial coverage rather than inventing zero usage.
- The scanner does not log bearer tokens or API response bodies to the summary report.
- GitHub data stays in a local SQLite DB and exported report files; keep the state directory on a private ext4/NTFS volume.
- Do not run untrusted workflow code, install a runner, change repository visibility, change billing limits, or publish checks in this phase.
- GitHub search previously returned 201 owner repositories (151 public/50 private). That is an **earlier metadata search observation**, not proof of V0.2 authenticated workflow/run scan coverage.
