# Kubernetes deployment

The current stack is one controller and one dedicated project-1 HTTP runner.
Both use the same image, with different entrypoints. There is no HPA or shared
worker queue: keep each Deployment at one replica with the `Recreate` strategy.

## Before applying

- Set the same new image tag in `deployment.yaml` and `runner.yaml` and update
  `IMAGE_TAG`. Build/push using `Dockerfile.optimized`.
- Default namespace is `brace`; update all manifests and endpoint Service DNS
  if changing it. The default worker serves project ID `1`. Match the actual
  database project ID, Deployment/Service labels/names and endpoint Secret.
- PVCs use the default StorageClass and RWO access. Set a supported class if the
  cluster has no default. SQLite WAL requires suitable filesystem locking; use
  block-backed storage for the config PVC. Existing PVC access modes are not
  automatically migrated: retain existing compatible PVCs instead of applying
  an incompatible spec change or deleting data volumes.
- The portable manifests use UID/GID/fsGroup `1001`. Verify that the CSI driver
  applies group permissions to mounted volumes. Existing data must be writable
  by that identity. OpenShift restricted SCCs may require your namespace's
  allocated UID/fsGroup ranges instead; adjust security contexts to allowed IDs
  and storage ownership, rather than granting privileged/root execution.
- Runner `/tmp` and `/dev/shm` use `emptyDir`; they contain temporary jobs and
  artifacts, not the database. Workers mount no controller PVCs or JWT/Fernet
  credentials and have no Kubernetes service-account token.
- Set the external `BRACE_PUBLIC_URL` to your Ingress/Route URL. Both scheduler
  and container timezones default to `Asia/Kolkata` in these examples.

## Apply

Use `kubectl`, or `oc` on OpenShift. Do not apply the whole directory: it contains
Secret placeholders, a legacy Job template and an optional OpenShift Route.

```bash
kubectl create namespace brace --dry-run=client -o yaml | kubectl apply -f -

# Fresh installation only: produces all three matching Secrets.
umask 077
bash k8s/gen-secret.sh > /tmp/brace-secrets.yaml
kubectl apply -f /tmp/brace-secrets.yaml
rm -f /tmp/brace-secrets.yaml

kubectl apply -f k8s/supporting.yaml
kubectl apply -f k8s/network-policy.yaml
kubectl apply -f k8s/runner.yaml
kubectl apply -f k8s/deployment.yaml

kubectl -n brace rollout status deployment/brace-runner-project-1
kubectl -n brace rollout status deployment/brace-rf-controller
kubectl -n brace get pods,services,pvc
kubectl -n brace logs deployment/brace-runner-project-1 --tail=50
kubectl -n brace logs deployment/brace-rf-controller --tail=50

# Portable local dashboard access
kubectl -n brace port-forward service/brace-rf-controller 8080:8080
```

Open `http://localhost:8080`. For OpenShift exposure, separately run
`oc apply -f k8s/route.yaml`. On standard Kubernetes, configure your own Ingress
and TLS; do not apply the OpenShift Route. The runner Service is internal only.

Do not rerun the secret generator during routine upgrades. It generates new
JWT/Fernet keys and runner credentials; replacing the encryption key prevents
decryption of existing profiles and other stored secrets. Preserve Secrets
and PVCs. For upgrades, stop submitting new work, disable or avoid scheduled
runs and wait for active jobs to finish before replacing pods. A 60-second
termination grace period does not implement worker draining or durable jobs.

## Runner network access

`network-policy.yaml` allows only controller ingress on port 8090 and cluster DNS
egress. Test-target traffic is denied until you add explicit approved egress
rules. Policies require a CNI that enforces NetworkPolicy; otherwise the policy
has no isolation effect. DNS selectors assume kube-dns/CoreDNS labelled
`k8s-app=kube-dns` in `kube-system`; adapt them for your DNS service or NodeLocal
DNS. Controller Git/SMTP/AI access remains subject to your cluster policies.

For a target pod in another namespace, add an egress rule with both the
namespace and pod selectors, and its target port. For external fixed-IP targets,
adapt the commented `ipBlock` example. Account for redirects, authentication
providers and changing destination IPs; standard NetworkPolicy cannot allow a
hostname directly. Add `hostAliases` to the **runner** if the approved target has
no DNS record. DNS resolution alone does not grant outbound connectivity.

## Additional projects and capacity

Duplicate the runner Deployment, Service, token Secret and project NetworkPolicy
for each project. Change the `brace-project` label, resource names and
`BRACE_RUNNER_PROJECT_ID`; add the corresponding Service URL/token to the
controller-only `brace-runner-endpoints` Secret. Restart affected workloads
after environment Secret updates. Never give a worker the endpoint-map Secret.

`BRACE_RUNNER_CAPACITY=3` permits three jobs inside the worker pod. The
controller's global test/run limits still apply across projects. Increase
worker CPU, memory and shared memory alongside concurrency; do not add runner
replicas behind a Service. Worker job state is pod-local, and status/cancel/
artifact calls must return to the owning pod. Durable queue/shared job state,
artifact storage, recovery and draining are prerequisites for autoscaling.

## Legacy files

`job-template.yaml`, `trigger-job.sh` and `sync-suites.sh` are retained for the
older standalone Job/shared-PVC workflow. They are not used by the current
controller's authenticated HTTP runner and do not populate its run records.
The template references an old runner image; do not substitute the current
controller image without selecting the correct standalone entrypoint.
Use UI upload or project Git sync for current project sources.

## Validation

Run `python tests/validate_k8s.py` in an environment with PyYAML for local YAML
and Deployment/Service/Secret/PVC cross-reference checks.

Before a real apply, check against your cluster APIs and admission policies:

```bash
kubectl apply --dry-run=server -f k8s/supporting.yaml
kubectl apply --dry-run=server -f k8s/network-policy.yaml
kubectl apply --dry-run=server -f k8s/runner.yaml
kubectl apply --dry-run=server -f k8s/deployment.yaml
```

Local YAML parsing and cross-reference validation do not confirm CSI ownership,
SCC admission, DNS, egress, image pulls or Chrome execution. Test a small Robot
suite after deployment and inspect runner logs and returned artifacts.
