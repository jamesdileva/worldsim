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
<venv-python> -c "from app.testers import TESTERS; \
from app.testers.features import FEATURES; \
print('Card-Game' in TESTERS, 'Card-Game' in FEATURES)"
```

(Tester-only integrations like Surfhop legitimately print False for
FEATURES; feature-only ones like Betsim print False for TESTERS.)

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

---

## 9. Velocity (Godot) integration notes

First non-node integration. Godot projects have no lockfile/manifest, so a
**shim `package.json`** carries the canonical commands; all of them route
through `tools\godot.cmd`, a locator wrapper resolving the engine exe in
order: `GODOT_EXE` env var → `godot` on PATH → newest winget install.

Verified ground truth (2026-08-22):

- launch: `tools\godot.cmd --path . -- --smoke` boots the real main menu,
  self-drives into the configured map (`--smoke-map=<id>`, default
  beginner), simulates ~8s of gameplay input, then **exits itself**
  (`RESULT=OK` + exit 0). Milestones are printed to stdout (live, per stage)
  and written to `%TEMP%\velocity_smoke.log`.
- stages emitted by the game: `MENU_SHOWN → MAP_SELECT_SHOWN →
  SETTINGS_SHOWN → MAP_LOAD_STARTED → PLAYER_SPAWNED → RUN_STARTED →
  GAMEPLAY_OK` (player must move >50u or the run fails). The tester gates
  on a subset — `MENU_SHOWN`, `MAP_SELECT_SHOWN`, `SETTINGS_SHOWN`,
  `RUN_STARTED`, then `RESULT=OK` (GAMEPLAY_OK implied) — check
  surfhop.py before assuming any marker is a test gate.
- stage screenshots: the game dumps its own framebuffer per stage to
  `%TEMP%\velocity_smoke_<stage>.png` (menu / map_select / settings /
  gameplay at run+4s; skipped headless) — immune to window-capture blanking
  and app-log tail lag. The tester registers these after RESULT and takes
  one live PrintWindow shot of the hold window as a sanity capture.
- port: none (single-process game; no backend).
- fallback: `--smoke-headless` script adds `--headless` for CI-style passes
  without rendering.
- cleanup: process exits by itself; if tree-killed, image name is the
  Godot engine exe (`Godot_v*.exe`) — `taskkill /IM Godot_v4.7.2-stable_win64.exe /T`,
  not the project name.
- sandbox notes: first-run state writes `user://save/settings.cfg` on this
  account (harmless); achievements unlock locally during smoke runs.

Command contract:

| Sentinel command | String | Pass condition |
|---|---|---|
| test | `tools\godot.cmd --headless --path . --script res://tests/test_runner.gd` | exit 0, stdout contains `ALL TESTS PASSED`, zero ERROR lines |
| start | `tools\godot.cmd --path . -- --smoke` | exit 0, `[smoke] RESULT=OK` |
| build | `tools\godot.cmd --headless --path . --editor --quit` | exit 0 |

Preflight results (2026-08-22): test ✅ 406 checks · start ✅ windowed +
headless · build ✅ · packaged-exe layout ⏳ deferred to release export
(target `dist/win-unpacked/`). DOM feature-testers are N/A — no CDP/DOM in
Godot; the smoke mode replaces click-through assertions.

### Lessons from live Sentinel integration (2026-08-23)

Findings from wiring the custom "Velocity smoke" tester end-to-end; kept here
because they generalize to any Godot project in Sentinel:

- **Stale persisted commands**: a project row caches `stack.commands` from
  extraction time; later `package.json` edits do NOT propagate (build/open
  self-heal via live rediscovery, testers don't). Custom testers must re-read
  the shim per run (surfhop.py does), or restart the backend after manifest
  edits.
- **Restart after code changes**: uvicorn loads tester modules at import time;
  editing a tester requires killing the 8420 listener and relaunching before
  API-triggered runs pick it up.
- **Scheduler starvation**: job pool is `ThreadPoolExecutor(pool_size=2)` with
  no watchdog — one wedged subprocess blocks all future jobs until restart.
  Stale buildlog rows stuck at "running" used to survive restarts and
  re-stick the Builds tab on "Working…" forever; since 2026-08-23 a startup
  sweep marks orphaned rows failed (`BuildLogRepository.mark_orphaned_as_failed()`,
  wired into the lifespan), so every restart self-heals all projects.
- **API route gotcha**: `/api/v1/projects` without a trailing slash silently
  falls through to the SPA catch-all and returns HTML. Prefer direct SQLite
  reads over PowerShell JSON parsing for verification.
- **Window capture on Godot**: exe-path window matching can't see the engine
  (winget installs under a versioned package dir); match by title (`^Velocity`)
  instead.
- **PrintWindow blanking**: GPU-composited 3D content intermittently captures
  black via PrintWindow; surfhop.py falls back to an ImageGrab screen crop of
  the window rect when frames come back blank.
- **Screenshot timing needs engine cooperation**: milestones printed only at
  run end, so every log gate fired ~30s late and shots photographed the wrong
  stage (menu shot caught mid-map gameplay). Fix shipped in-game: markers now
  print LIVE per stage plus a `RUN_STARTED` marker; `--smoke-stage-pause=<s>`
  dwells on menu/load/spawn so capture photographs the actual stage;
  `--smoke-hold=<s>` keeps the window alive after RESULT for post-pass shots.
- **Log-gate lag makes timed external captures unreliable**: even with live
  markers, app-log tail lag (seconds) means a gate-then-shoot pattern lands
  after the game moved on — map_select shots caught the settings overlay,
  settings shots caught the map load. Definitive fix: the GAME dumps its own
  framebuffer per stage (`_smoke_capture`, awaited so it photographs the
  current stage, skipped headless) and the tester registers the PNGs after
  RESULT. External window capture is now only the post-pass hold shot.
  Corollary: fire-and-forget capture coroutines photograph the NEXT stage —
  always await them.
- Verified end state: session PASSED with five correctly-labeled screenshots
  (main menu / map select / settings / mid-run motion at speed / post-run
  hold frame).
