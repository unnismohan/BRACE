#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# ARIA RF Runner — Container Entrypoint
#
# Runs Robot Framework. Chrome uses --headless=new (no Xvfb needed).
# Handles OpenShift random UID assignment gracefully.
#
# Environment variables (passed by K8s Job):
#   RF_SUITE        path to suite(s) to run, space-separated
#   RF_EXTRA_ARGS   additional robot arguments (optional)
#   RUN_ID          unique run identifier (for results naming)
#   BSS_ENV         target environment (prod/staging/etc)
#   RESULTS_DIR     where to write output.xml + screenshots
#                   default: /opt/rf/results
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

# ── OpenShift random UID fix ──────────────────────────────────
# OpenShift assigns a random UID at runtime (e.g. 1000580000)
# Many tools need a valid /etc/passwd entry for the current UID
if ! whoami &>/dev/null; then
    if [ -w /etc/passwd ]; then
        echo "rfuser:x:$(id -u):0:RF User:/opt/rf:/bin/bash" >> /etc/passwd
    fi
fi

echo "════════════════════════════════════════"
echo " ARIA RF Runner"
echo " Run ID  : ${RUN_ID:-unknown}"
echo " Suite   : ${RF_SUITE:-not set}"
echo " Env     : ${BSS_ENV:-prod}"
echo " User    : $(id)"
echo "════════════════════════════════════════"

# ── Validate required env vars ────────────────────────────────
if [ -z "${RF_SUITE:-}" ]; then
    echo "ERROR: RF_SUITE environment variable is required"
    echo "Example: RF_SUITE=suites/billing/ui/invoice_portal.robot"
    exit 1
fi

RESULTS_DIR="${RESULTS_DIR:-/opt/rf/results}"
RUN_ID="${RUN_ID:-run-$(date +%Y%m%d-%H%M%S)}"
# Each run gets its own subdir — prevents collisions between concurrent jobs
RESULTS_DIR="${RESULTS_DIR}/${RUN_ID}"
mkdir -p "$RESULTS_DIR"

# ── Configure Chrome options ──────────────────────────────────
export CHROME_FLAGS="--no-sandbox --disable-dev-shm-usage --window-size=1920,1080"

# ── Run Robot Framework ───────────────────────────────────────
echo "[1/3] Running Robot Framework..."
echo "  Suite   : $RF_SUITE"
echo "  Results : $RESULTS_DIR"
echo "  Extra   : ${RF_EXTRA_ARGS:-none}"

robot \
    --outputdir "$RESULTS_DIR" \
    --output   output.xml \
    --log      log.html \
    --report   report.html \
    --variable RUN_ID:"$RUN_ID" \
    --variable BSS_ENV:"${BSS_ENV:-prod}" \
    --variable CHROME_FLAGS:"$CHROME_FLAGS" \
    --variable RESULTS_DIR:"$RESULTS_DIR" \
    ${RF_EXTRA_ARGS:-} \
    $RF_SUITE

RF_EXIT=$?

echo "[2/3] RF execution complete (exit=$RF_EXIT)"
echo "  Results written to: $RESULTS_DIR"
ls -lh "$RESULTS_DIR/" 2>/dev/null || true

# ── Summary ───────────────────────────────────────────────────
if [ "$RF_EXIT" -eq 0 ]; then
    echo "  Status: ALL TESTS PASSED"
elif [ "$RF_EXIT" -eq 1 ]; then
    echo "  Status: SOME TESTS FAILED (output.xml available for analysis)"
else
    echo "  Status: RF EXECUTION ERROR (exit=$RF_EXIT)"
fi

# Exit 0 always — let Agent 1 parse output.xml to determine pass/fail
# K8s Job should not fail just because tests fail
exit 0
