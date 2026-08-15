#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# BRACE RF Controller Entrypoint
# Starts FastAPI controller (Chrome runs --headless=new, no Xvfb)
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

# OpenShift random UID fix
if ! whoami &>/dev/null; then
    if [ -w /etc/passwd ]; then
        echo "rfuser:x:$(id -u):0:RF User:/opt/rf:/bin/bash" >> /etc/passwd
    fi
fi

echo "════════════════════════════════════════"
echo " BRACE RF Controller"
echo " User    : $(id)"
echo " Suites  : ${SUITES_DIR:-/opt/rf/suites}"
echo " Results : ${RESULTS_DIR:-/opt/rf/results}"
echo "════════════════════════════════════════"

export HOME=/tmp
mkdir -p "${RESULTS_DIR:-/opt/rf/results}"

# ── Start Xvfb so Chrome can run non-headless inside container ────
echo "[1/2] Starting Xvfb on :99..."
Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp &
XVFB_PID=$!
sleep 1
if ! kill -0 $XVFB_PID 2>/dev/null; then
    echo "WARNING: Xvfb failed to start — Chrome tests may fail"
fi

# ── Start FastAPI controller ──────────────────────────────────────
echo "[2/2] Starting RF controller on :8080..."
cd /opt/rf/controller
exec python -m uvicorn main:app \
    --host 0.0.0.0 \
    --port 8080 \
    --workers 1 \
    --log-level info
