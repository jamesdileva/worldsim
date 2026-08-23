# New-Project Integration — Playbook (docs/integration.md)

How a project earns its place in Sentinel, from zero-effort indexing to a
full click-through feature. This is a **reusable engineering playbook**,
not just a checklist: every rule below was paid for with a real bug on a
real integration (Betsim, v1.17.19.x era). Copy this file into new projects
and follow it top to bottom.

---

## 1. Tier 0 contract — what Sentinel actually extracts and runs

Sentinel's command extractor reads your **root manifests** and derives:

| Command | Source | How Sentinel executes it |
|---------|--------|--------------------------|
| `install` | **Hardcoded** by lockfile (`npm install` / `pnpm install` / …) | Your custom `install` script is NOT the extracted command. It only matters as an npm lifecycle hook that runs during that `npm install` |
| `startup` | `scripts.dev \|\| scripts.start` | The **raw string inside the script**, executed as a bare shell line from the repo root — *outside npm, so `node_modules/.bin` is not on PATH* |
| `build` | `scripts.build \|\| scripts.dist` | Same raw-string execution |
| `test` | `scripts.test` | Same; run first by the default smoke |

### Command authoring rules (all four must obey)

1. Every script must work as a **bare shell line from the repo root with a
   neutralized PATH**. Never rely on `node_modules/.bin` being resolvable.
   - ❌ `"dev": "concurrently -n …"` → `'concurrently' is not recognized`
   - ✅ `"dev": "npx concurrently -n …"` (`npx` ships with Node)
2. Python must come from your project's venv by absolute-relative path:
   - ✅ `".venv\\Scripts\\python -m pytest"` 
3. Cross-package calls go through npm so each child gets a sane environment:
   - ✅ `"test": ".venv\\Scripts\\python -m pytest backend && npm run test --prefix frontend"`
4. **Fresh-clone `npm install` must go green unattended.** If a lifecycle
   hook needs a venv, create it in-script:
   - ✅ `"install": "if not exist .venv python -m venv .venv && .venv\\Scripts\\python -m pip install -r backend/requirements.txt"`

## 2. Packaging layout (what launcher_detect can find)

Sentinel finds packaged exes only at these layouts, relative to repo root:

- `release/win-unpacked/*.exe`
- `dist/win-unpacked/*.exe`
- `frontend/dist_electron/win-unpacked/*.exe`, `frontend/dist/win-unpacked/*.exe`
- Tauri: `out/*.exe`, `src-tauri/target/release/*.exe`

Noise excluded by name: installers/`Setup`, `*.blockmap`, `elevate.exe`,
`python*.exe`, `venv*`.

**Gotcha:** electron-builder resolves output paths relative to *where you
run it* (the package dir), not the repo root. A `frontend/`-packaged app
must set `"directories": {"output": "../release"}` to land at
`release/win-unpacked/`. Verify after building:

```
Test-Path <repo-root>\release\win-unpacked\<YourApp>.exe   # must be True
```

## 3. Electron main-process checklist

Every item here was a real production bug:

| # | Rule | Symptom if violated |
|---|------|--------------------|
| 1 | If `package.json` has `"type": "module"`, the main script must be **`.cjs`** (and `"main"` + builder `files` updated to match) | Packaged app crashes instantly: "require is not defined in ES module scope". Dev mode hides it because Electron loads the file path directly |
| 2 | Vite config needs `base: "./"` for apps loaded over `file://` | Blank white window — assets resolve to `/assets/...` which doesn't exist on disk |
| 3 | Router must be **HashRouter**, not BrowserRouter, for `file://` apps | UI shell renders but *every route* shows the catch-all ("Page not found") — the pathname is the exe's file path |
| 4 | Derive repo root from `app.getPath("exe")`, never `__dirname` | Backend/resources not found when packaged — `__dirname` lives inside `app.asar` |
| 5 | FastAPI CORS must accept `Origin: null` (a `file://` renderer sends exactly that) | API calls fail only in the packaged app; dev works fine |
| 6 | Prefer the self-spawning-backend pattern: packaged exe parses `--user-data-dir` from argv, spawns the backend from a sibling `.venv` with `BETSIM_DB_PATH` pointed inside that sandbox, waits on `/health`, then loads the renderer | Otherwise the exe renders a dead UI and click-throughs assert nothing real |
| 7 | Cleanup contract: the spawned backend must die with the app. Sentinel tree-kills by image name (`taskkill /IM <App>.exe /T`) — spawn the backend as a child of the exe so `/T` reaches it | Orphaned servers hold ports and poison later runs |
| 8 | Log startup milestones to a temp file (`spawn attempted`, `healthy`, `renderer loaded`) | Without this, diagnosing packaged-only failures means guessing |

## 4. Slug registry — casing is load-bearing

The testers/features registries key off `_slug(project.name)` where
`project.name` comes from Sentinel's DB (the indexer title-cases folder
names: `betsim` → `Betsim`). Matching is **case-sensitive**:

- Real incident: DB row `Resmaker` vs registry key `ResMaker` — features
  silently orphaned, plus duplicate-looking rows.
- Before registering, query the live DB:
  `SELECT name FROM project WHERE path LIKE '%<folder>%'` and use that
  exact string.
- Feature-only registration (no smoke tester): add the module import +
  registry entry + slug to `FEATURE_ONLY_SLUGS`. The registry's import
  gate fails loudly on mismatches — run it before committing:

```bash
<venv-python> -c "from app.testers.features import FEATURES; \
print('Betsim' in FEATURES)"
```

Also purge test-junk rows occasionally — Tier tests create Project rows in
pytest temp dirs and leave them behind (children first, FK-safe).

## 5. Feature authoring recipe (electron=True)

The runner relaunches the packaged exe with CDP + a sandboxed
`--user-data-dir`; `ctx.page` drives the real DOM (clicks/fills fine,
`ctx.go()` refused).

1. **Fresh sandbox = fresh localStorage + fresh DB.** Anything gated on
   first-run state (onboarding modals!) shows every run — dismiss it
   deterministically first, or it covers the screen you're asserting.
2. **Wait for self-spawned backends** before asserting data (poll
   `/api/health` via `ctx.page.request`); warm-up is a few seconds and
   manual-speed clicks lose that race.
3. Anchor assertions on `data-testid`s you shipped in the app — they're
   stable across copy edits.
4. Destructive actions only against self-created entities; clean up after.
5. Budget: default 120s per feature; heavy first-paints may need more.
6. Write the facts block (template below) into the module docstring —
   verified live, never guessed.

## 6. Pitfalls quick reference

| Symptom | Cause | Fix |
|---------|-------|-----|
| `'concurrently' is not recognized` on smoke launch | Raw startup string executed outside npm context | Use `npx <localbin>` or full paths in scripts |
| `npm install` red on fresh clone | Lifecycle hook assumes pre-made venv | Create venv inside the hook |
| Packaged exe dies instantly, ESM error | `.js` main under `"type": "module"` | Rename main to `.cjs`, update `main` + builder files |
| Blank white window | Vite default `base: "/"` over `file://` | Set `base: "./"` |
| "Page not found" on all tabs | BrowserRouter under `file://` | Switch to HashRouter |
| Backend unreachable only when packaged | CORS rejects `Origin: null` / wrong root detection | Allow null origin; root from exe path |
| Features never attach to a project | Registry slug casing ≠ DB name | Query DB; match exactly |
| Junk projects in dashboard | Tier-test leftovers | Periodic FK-safe purge (backup first) |

## 7. Preflight checklist (before registering anything)

- [ ] Fresh clone → `npm install` exits 0 unattended
- [ ] Extracted `startup` string works via bare `cmd /c "<string>"` from repo root
- [ ] Extracted `test` passes headless
- [ ] `npm run build` succeeds
- [ ] Packaged exe exists at a launcher_detect layout
- [ ] Double-clicking the exe opens a working app (backend included or gracefully degraded)
- [ ] Live DB queried for the exact project `name`; slug registered with that casing
- [ ] Registry import gate passes; targeted tests green

## 8. Facts-block template

Copy into each tester/feature module docstring:

```
Verified ground truth (<date>):
- launch: <who launches what> (FeatureRunner-owned for electron=True)
- port <N>, <auth or none>
- fallback: <behavior when primary path is unavailable>
- cleanup: taskkill /IM <App>.exe /T
- sandbox notes: <first-run state, hermetic DB, etc.>
```
