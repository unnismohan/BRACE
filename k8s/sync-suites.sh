#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# Sync RF automation scripts into aria-rf-suites-pvc
#
# Run from the RF-testing repo root on any machine with oc access.
# Spins up a temporary pod that mounts the PVC, copies files, exits.
#
# Usage:
#   ./k8s/sync-suites.sh [local-scripts-dir]
#
# Default local-scripts-dir: ./alpha-automation-scripts/Impl-scripts
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOCAL_SCRIPTS="${1:-${REPO_ROOT}/alpha-automation-scripts/Impl-scripts}"
NAMESPACE="brace"
PVC="brace-rf-suites-pvc"
SYNC_POD="suite-sync-$(date +%s)"

echo "════════════════════════════════════════"
echo " BRACE Suite Sync"
echo " Source : ${LOCAL_SCRIPTS}"
echo " Target : ${NAMESPACE}/${PVC}"
echo "════════════════════════════════════════"

# ── Verify source exists ────────────────────────────────────────
if [[ ! -d "$LOCAL_SCRIPTS" ]]; then
    echo "ERROR: local scripts dir not found: ${LOCAL_SCRIPTS}"
    exit 1
fi

# ── Spin up sync pod mounting PVC ───────────────────────────────
echo ""
echo "[1/3] Starting sync pod..."
oc run "${SYNC_POD}" \
    -n "${NAMESPACE}" \
    --image=registry.example.com:5006/tools/busybox:1.32.0 \
    --restart=Never \
    --overrides="{
      \"spec\": {
        \"containers\": [{
          \"name\": \"sync\",
          \"image\": \"registry.example.com:5006/tools/busybox:1.32.0\",
          \"command\": [\"sh\", \"-c\", \"sleep 300\"],
          \"resources\": {
            \"requests\": {\"cpu\": \"100m\", \"memory\": \"128Mi\"},
            \"limits\":   {\"cpu\": \"200m\", \"memory\": \"256Mi\"}
          },
          \"volumeMounts\": [{
            \"name\": \"suites\",
            \"mountPath\": \"/opt/rf/suites\"
          }],
          \"securityContext\": {
            \"allowPrivilegeEscalation\": false,
            \"runAsNonRoot\": true,
            \"runAsUser\": 1001,
            \"capabilities\": {\"drop\": [\"ALL\"]}
          }
        }],
        \"volumes\": [{
          \"name\": \"suites\",
          \"persistentVolumeClaim\": {\"claimName\": \"${PVC}\"}
        }],
        \"securityContext\": {
          \"runAsNonRoot\": true,
          \"seccompProfile\": {\"type\": \"RuntimeDefault\"}
        }
      }
    }" \
    --command -- sh -c "sleep 300"

echo "  Waiting for sync pod ready..."
oc wait pod "${SYNC_POD}" -n "${NAMESPACE}" --for=condition=Ready --timeout=60s

# ── Copy modules (delete first to ensure clean replace) ──────────
echo ""
echo "[2/3] Copying modules..."
for MODULE_DIR in "${LOCAL_SCRIPTS}"/*/; do
    MODULE=$(basename "$MODULE_DIR")
    echo "  Replacing ${MODULE}..."
    oc exec "${SYNC_POD}" -n "${NAMESPACE}" -- sh -c "rm -rf /opt/rf/suites/${MODULE}" 2>/dev/null || true
    oc cp "${MODULE_DIR}" "${NAMESPACE}/${SYNC_POD}:/opt/rf/suites/${MODULE}"
    oc exec "${SYNC_POD}" -n "${NAMESPACE}" -- sh -c "chmod -R 777 /opt/rf/suites/${MODULE}"
done

echo "  Verifying..."
oc exec "${SYNC_POD}" -n "${NAMESPACE}" -- sh -c "find /opt/rf/suites -maxdepth 2 -type d | sort"

# ── Cleanup ─────────────────────────────────────────────────────
echo ""
echo "[3/3] Cleaning up sync pod..."
oc delete pod "${SYNC_POD}" -n "${NAMESPACE}" --ignore-not-found

echo ""
echo "✓ Sync complete — suites available in brace-rf-suites-pvc"
echo ""
echo "Next: bash k8s/trigger-job.sh UPC"
