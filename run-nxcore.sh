#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export AEROFORGE_STATE="${AEROFORGE_STATE:-/srv/AV-MEMORY/aeroforge}"
# Explicit allowlist, no arbitrary paths. Can be narrowed by operator.
export AEROFORGE_ALLOWED_ROOTS="${AEROFORGE_ALLOWED_ROOTS:-/srv/ACOS/AeroForge:/srv/ACOS/_agent_worktrees}"
cd "$ROOT"
exec python3 -m aeroforge "$@"
