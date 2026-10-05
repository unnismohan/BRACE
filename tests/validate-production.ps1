param([string]$Image='brace:isolated')
$ErrorActionPreference='Stop'
if(-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker unavailable: production validation cannot run.' }
docker build -f Dockerfile.optimized -t $Image .
if($LASTEXITCODE) { throw 'Production image build failed' }
docker run --rm --read-only --tmpfs /tmp:rw,size=512m --shm-size 256m --cap-drop ALL --security-opt no-new-privileges:true --entrypoint /bin/bash $Image -c 'export HOME=/tmp; Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp & sleep 1; python /opt/rf/controller/production_smoke.py'
if($LASTEXITCODE) { throw 'Production Chrome/Robot validation failed' }
