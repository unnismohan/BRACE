#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# BRACE RF Test Trigger
#
# Usage:
#   ./k8s/trigger-job.sh <module> [suite-subpath] [extra-rf-args]
#
# Examples:
#   ./k8s/trigger-job.sh UPC
#   ./k8s/trigger-job.sh UPC Testcases/MySuite/TC_001_Example.robot
#   ./k8s/trigger-job.sh SLM "" "--include smoke"
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

NAMESPACE="brace"
MODULE="${1:-UPC}"
SUITE_SUBPATH="${2:-Testcases/${MODULE}}"
RF_EXTRA_ARGS="${3:-}"
BSS_ENV="${BSS_ENV:-staging}"

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
RUN_ID="${MODULE,,}-${TIMESTAMP}"
RF_SUITE="/opt/rf/suites/${MODULE}/${SUITE_SUBPATH}"
JOB_NAME="brace-rf-${RUN_ID}"

echo "════════════════════════════════════════"
echo " BRACE RF Job Trigger"
echo " Module  : ${MODULE}"
echo " Suite   : ${RF_SUITE}"
echo " Run ID  : ${RUN_ID}"
echo " Job     : ${JOB_NAME}"
echo " NS      : ${NAMESPACE}"
echo "════════════════════════════════════════"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="${SCRIPT_DIR}/job-template.yaml"

RENDERED=$(sed \
    -e "s|\${RUN_ID}|${RUN_ID}|g" \
    -e "s|\${BSS_SERVICE}|${MODULE,,}|g" \
    -e "s|\${RF_SUITE}|${RF_SUITE}|g" \
    -e "s|\${BSS_ENV}|${BSS_ENV}|g" \
    -e "s|\${RF_EXTRA_ARGS}|${RF_EXTRA_ARGS}|g" \
    -e "s|brace-rf-\${RUN_ID}|${JOB_NAME}|g" \
    "${TEMPLATE}")

echo ""
echo "[1/3] Creating Job..."
echo "$RENDERED" | oc apply -f -

echo ""
echo "[2/3] Waiting for pod..."
sleep 5
POD=$(oc get pod -n "${NAMESPACE}" -l "run-id=${RUN_ID}" -o name 2>/dev/null | head -1)

if [[ -z "$POD" ]]; then
    echo "  Pod not ready yet — check: oc get pods -n ${NAMESPACE} -l run-id=${RUN_ID}"
else
    oc wait "${POD}" -n "${NAMESPACE}" --for=condition=Ready --timeout=120s 2>/dev/null || true

    echo ""
    echo "[3/3] Streaming logs (Ctrl+C to detach — job continues)..."
    oc logs -n "${NAMESPACE}" -f "${POD}" || true
fi

echo ""
JOB_STATUS=$(oc get job "${JOB_NAME}" -n "${NAMESPACE}" \
    -o jsonpath='{.status.conditions[?(@.type=="Complete")].status}' 2>/dev/null || echo "")

if [[ "$JOB_STATUS" == "True" ]]; then
    echo "✓ PASSED — results in brace-rf-results-pvc/${RUN_ID}/"
else
    FAILED=$(oc get job "${JOB_NAME}" -n "${NAMESPACE}" \
        -o jsonpath='{.status.conditions[?(@.type=="Failed")].status}' 2>/dev/null || echo "")
    if [[ "$FAILED" == "True" ]]; then
        echo "✗ FAILED — check logs above"
        exit 1
    else
        echo "? Still running — oc get job ${JOB_NAME} -n ${NAMESPACE}"
    fi
fi
