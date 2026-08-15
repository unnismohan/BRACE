### What this changes

### Why

### How it was verified
Be specific — "ran a real 3-case suite, watched it execute 3-wide, checked the
combined report" beats "tested locally".

- [ ] Python parses: `python -c "import ast,glob; [ast.parse(open(f,encoding='utf-8').read()) for f in glob.glob('controller/*.py')]"`
- [ ] JS parses: `for f in controller/static/js/*.js; do node --check "$f"; done`
- [ ] Browser console clean on the affected page
- [ ] README / in-app manual updated if behaviour changed
- [ ] No new runtime dependency (or justified above)
