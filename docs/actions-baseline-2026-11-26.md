# AeroForge Actions audit — November 26, 2026

**Source:** authenticated read-only GitHub REST inventory. **Status:** evidence-backed, with historical usage limitations. No GitHub workflows were started or modified.

## Workflow-inventory 404s

The previous 41 workflow retrieval errors have been classified as HTTP 404 responses from the GitHub Contents API. An independent default-branch Git tree check confirmed **all 41 files absent** from untruncated default-branch trees. This supports stale workflow-file references rather than API authorization failure or 41 failed builds. GitHub workflow metadata is still present. No records were deleted.

| Repository | Missing YAML files |
|---|---:|
| PondyFlats | 18 |
| ACOS | 8 |
| signal | 5 |
| lot-assessment | 4 |
| AV-Portrait | 1 |
| RydeSync | 1 |
| aa-cards | 1 |
| av-setup | 1 |
| mag | 1 |
| nxcore | 1 |
| **Total** | **41** |

## Private repository baseline

All 51 private repositories were successfully queried for the last 30 days of run counts.

- **749** workflow run records across private repositories
- **647** in ACOS (**86.4%** of all private run records)
- **47** in aerovista-catalog-console
- **20** in lenora-voss
- **17** in aerovista-commerce-api
- **8** in EchoVerseStudio
- **4** in Summit_Love_Is_Hard_Master_Package
- **4** in nxcore
- **2** in av-setup

These are workflow **run counts**, not jobs, billed minutes, or dollars.

## ACOS: all 647 run records

475 pull-request runs, 172 push runs. Eight workflows contributed:

| Workflow | Runs | Push | Pull request |
|---|---:|---:|---:|
| AVCC CI | 125 | 18 | 107 |
| Identity Experience CI | 102 | 11 | 91 |
| AeroCore App Adapter | 100 | 11 | 89 |
| Rack Identity Canary CI | 99 | 12 | 87 |
| ByteCast trusted training regression | 97 | 55 | 42 |
| Ascension Auth Bridge | 93 | 54 | 39 |
| Messenger | 17 | 1 | 16 |
| Mag Auth CI | 14 | 10 | 4 |

There were **87 observed same-workflow/same-SHA push-and-PR groups**. Those are review candidates, not proven waste: workflow events and PR merge commit SHA semantics must be accounted for.

## Expanded targeted sample of eight repositories

**124.47 observed job wall-minutes** across a capped, overlapping sample. These numbers are not GitHub-billed minutes and MUST NOT be added to the earlier 60.5-minute account sample.

| Repository | Sampled runs | Job wall-minutes |
|---|---:|---:|
| cdatonight | 20 | 34.78 |
| aerovista-onboarding- | 20 | 13.85 |
| lot-assessment | 20 | 17.10 |
| the-rack | 20 | 13.07 |
| bland | 10 | 10.47 |
| ACOS | 20 | 17.88 |
| lenora-voss | 20 | 1.45 |
| aa-cards | 20 | 15.87 |

## Recommended next changes

1. Prioritize ACOS (86.4% of private run records). Review feature-branch push plus PR overlap. Maintain required security and production release checks.
2. Investigate ByteCast and Ascension workflows with broad push branches. Propose main-only push plus PR checks, and retain explicit manual validation where appropriate; do not merge without reviewing required check policies.
3. Apply superseding-run concurrency only to PR checks, not production release jobs.
4. Review aerovista-catalog-console next. Do not spend effort on repositories with no observed runs.
5. Keep stale workflow references as audit findings. Do not automatically disable historical workflows.

## Private evidence (NXCore)

- /srv/AV-MEMORY/aeroforge/reports/inventory-diagnostics-2026-11-26.json
- /srv/AV-MEMORY/aeroforge/reports/inventory-default-branch-check.json
- /srv/AV-MEMORY/aeroforge/reports/private-run-counts-2026-11-26.json
- /srv/AV-MEMORY/aeroforge/reports/acos-run-breakdown-2026-11-26.json
- /srv/AV-MEMORY/aeroforge/reports/targeted-usage-2026-11-26.json

**Limitations:** This is metadata and sampled job runtime, not an authoritative GitHub billing invoice. No source changes or GitHub Actions jobs were initiated by these read-only scans.
