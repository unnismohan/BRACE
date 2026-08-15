# Screenshots

Referenced from the project README with relative paths, so they render on
GitHub, on GitLab, and in an offline clone.

## Capturing

- Browser at **1440×900**, zoom 100%. Wider shots become unreadable thumbnails.
- Use the example project — never a real one. Screenshots leak hostnames,
  project names, colleagues' usernames and test-case titles into a public repo,
  and they are permanent once pushed.
- Take them **after** a run has finished so the charts contain real data. Empty
  states make the tool look unfinished.
- PNG for UI. Keep each under ~400 KB (`pngquant` or any optimiser) — the repo
  is cloned by people on slow links.

## Expected files

| File | Shows |
|------|-------|
| `runs.png`     | Runs list mid-execution, one run `running` |
| `failure.png`  | Run detail with the inline failure summary expanded |
| `coverage.png` | Reports → Coverage |
| `editor.png`   | Scripts tab, file tree + editor |
| `demo.gif`     | Optional: trigger → parallel run → failure summary, ~20s |
