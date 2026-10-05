#!/bin/bash
set -euo pipefail
: "${BRACE_RUNNER_TOKEN:?Runner credential required}"
: "${BRACE_RUNNER_PROJECT_ID:?Dedicated project required}"
export HOME=/tmp
Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp &
cd /opt/rf/controller
exec python -m uvicorn runner_worker:app --host 0.0.0.0 --port 8090 --workers 1
