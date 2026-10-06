"""Static Kubernetes wiring checks; run with a Python environment with PyYAML."""
from pathlib import Path
import json
import yaml

root = Path(__file__).resolve().parents[1]
docs = {}
for path in (root / 'k8s').glob('*.yaml'):
    for resource in yaml.safe_load_all(path.read_text(encoding='utf-8')):
        if resource:
            docs[(resource['kind'], resource['metadata']['name'])] = resource

controller = docs['Deployment', 'brace-rf-controller']
runner = docs['Deployment', 'brace-runner-project-1']
secrets = {name: resource for (kind, name), resource in docs.items() if kind == 'Secret'}
pvcs = {name for kind, name in docs if kind == 'PersistentVolumeClaim'}
for deployment in [controller, runner]:
    assert deployment['spec']['replicas'] == 1
    assert deployment['spec']['strategy']['type'] == 'Recreate'
    pod = deployment['spec']['template']['spec']
    assert pod['automountServiceAccountToken'] is False
    container = pod['containers'][0]
    assert container['securityContext']['readOnlyRootFilesystem'] is True
    for env in container['env']:
        ref = env.get('valueFrom', {}).get('secretKeyRef')
        if ref:
            assert ref['name'] in secrets
            assert ref['key'] in secrets[ref['name']]['stringData']
    for volume in pod['volumes']:
        if 'persistentVolumeClaim' in volume:
            assert volume['persistentVolumeClaim']['claimName'] in pvcs
    service = docs['Service', deployment['metadata']['name']]
    labels = deployment['spec']['template']['metadata']['labels']
    assert all(labels.get(k) == v for k,v in service['spec']['selector'].items())

cc = controller['spec']['template']['spec']['containers'][0]
rc = runner['spec']['template']['spec']['containers'][0]
assert cc['image'] == rc['image']
env = {value['name']: value.get('value') for value in cc['env']}
assert env['BRACE_RUNNER_MODE'] == 'remote'
assert env['BRACE_REQUIRE_ISOLATION'] == 'true'
assert rc['command'] == ['/bin/bash','/opt/rf/controller/entrypoint-runner.sh']
assert not any('persistentVolumeClaim' in v for v in runner['spec']['template']['spec']['volumes'])
assert {value['name'] for value in rc['env']}.isdisjoint({'JWT_SECRET','BRACE_ENCRYPT_KEY','BRACE_RUNNER_ENDPOINTS'})
endpoints = json.loads(secrets['brace-runner-endpoints']['stringData']['endpoints'])
assert endpoints['1']['token'] == secrets['brace-runner-project-1']['stringData']['token']
assert endpoints['1']['url'] == 'http://brace-runner-project-1.brace.svc.cluster.local:8090'
assert len(list(yaml.safe_load_all((root/'k8s/deployment.yaml').read_text(encoding='utf-8')))) == 2
assert docs['NetworkPolicy','brace-project-1-runner']['spec']['podSelector']['matchLabels'] == runner['spec']['selector']['matchLabels']
print('Kubernetes YAML and deployment/Service/Secret/PVC wiring checks passed.')
