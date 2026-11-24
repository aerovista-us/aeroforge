# AeroForge V0.1 design

**Trust boundary:** GitHub API/fixture data and repository files are untrusted. Parsing and static scanning are allowed; shell execution and credentialed operations are out of scope.

```text
GitHub REST (GET) / JSON fixture
           |
       ForgeBridge   [read-only inventory]
           |
       ForgeBudget   [advisory finding rules]
           |
       ForgeLedger   [SQLite + evidence]
          / \
  ForgeDeck ForgeCore [view + durable queue]
                 |
             ForgeRunner [one static job at a time]
                 |
             ForgeGuard [path/size/time controls]
```

## States
Queued → Running → Passed / Failed / Blocked. A passed static check is **not** a passing integration test or production release. Work is completed only within the narrow contract of the selected static profile.

## Filesystem and permissions
Source lives at `/srv/ACOS/AeroForge` (ACOS shared path). Runtime data lives at `/srv/AV-MEMORY/aeroforge` for safe SQLite journaling. Worker must run as an unprivileged user, not root. Allowlisted roots gate all local target paths. GitHub credentials are never persisted.

## Findings
Rules carry severity, confidence and explicit recommendations. No invented dollar/minute savings. Workflows are upserted by repo+path, and old findings for a rescanned workflow are replaced. V0.1 doesn't delete stale workflows from a repository that GitHub no longer lists; a full lifecycle reconciliation is future work.

## Approval gates for future arbitrary code execution
- Separate, rootless container or dedicated VM; no host Docker socket.
- Read-only checkout snapshot, least-privilege ephemeral credentials, network egress policy.
- CPU/memory/pid/time restrictions and serial worker admission.
- Explicit per-project execution profile and owner approval.
- Artifact hashing, log redaction, Aegis-style independent acceptance.
- No automatic code merge or production deployment.

## GitHub Actions budget note
There is intentionally **no active Actions workflow** in the repository. The command-line unit tests are run locally. GitHub-hosted runner billing restrictions do not prevent Git version control, manual reviews, or direct source commits.
