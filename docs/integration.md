# New-Project Integration — Checklist & Facts (docs/integration.md)

How a project earns its place in Sentinel, from zero-effort indexing to a
full click-through feature. Written after the v1.17.17.1 batch (AG viewer
screenshot, Airadio screenshot feature, FinSight HTTP tester) so future
integrations follow a proven path instead of re-deriving ground truth.

## Tier 0 — automatic, zero effort

Every indexed project gets, with no code:

| Capability | How | Where |
|------------|-----|-------|
| Discovery + indexing | `git ls-files` (or fallback walk) from `SENTINEL_WATCH_DIRS`; noise dirs pruned | indexer |
| Commands | Manifest extractors (package.json, pyproject, …) → install/build/test/startup | `backend/app/utils/command_extractor.py` |
| Build / Tests pages | Run the discovered commands, deterministic, logged | dashboard |
| Default smoke | Launch startup → wait 20 s → crash-signature scan → screenshot; plus the discovered `test` command up front (v1.17.17.1) | `backend/app/testers/default_smoke.py` |
| App log | `data/logs/apps/<slug>.log` — every launch/CLI output lands here | `app_sessions._apps_dir()` |
| Session screenshots | Whole-desktop or window capture per run | Sessions page |

A project with a stored `test` command now has it **run first** by the
default smoke; a red suite fails the run before anything launches
(expect_exit=0, output in the app log).

## Tier 1 — custom tester (HTTP- or CLI-based, no mouse)

Add `backend/app/testers/<slug>.py` exporting `TESTER = Tester(...)` and
register it in `backend/app/testers/__init__.py`. Facts to verify **live
before writing code** (this is the whole point of the facts block):

1. Launch command that actually works from the project root (watch for
   `cd <subdir>` requirements — FinSight's root `package.json` has no
   `main`, so the real launch is `cd electron && electron .`).
2. Port + whether auth is needed (FinSight: none — GET / renders the
   dashboard directly).
3. What the app serves on GET (HTTP testers assert status + body marker).

Verified ground-truth block template (copy into the module docstring):

```
Verified ground truth (<date>):
- launch: <command> (cwd quirk if any)
- port <N>, <auth or none>
- fallback: <when the primary launch path is unreliable>
```

Prefer HTTP/CLI assertions (dinner-menu, FinSight, Algo-Trader patterns) —
headless, no mouse, no desktop state. Use `TesterEnvError` for env gaps,
`TesterAssertionError` for app failures.

## Tier 2 — click-through feature (native or Electron, window-targeted)

Add `backend/app/testers/features/<slug>.py` exporting `FEATURES = [...]`
and register in `backend/app/testers/features/__init__.py`.

### Engine choice

| Engine | When | Mouse? |
|--------|------|--------|
| HTTP / CLI tester | Anything served on a port, or CLI-verifiable | none |
| Native feature (`native=True`, pywinauto DesktopApp) | The app's own tkinter/pygame/SDL window | only if clicks are needed |
| Electron feature (`electron=True`, CDP) | Packaged electron apps needing DOM interaction | none (CDP synthetic) |
| Browser feature (Playwright Edge) | Browser-served apps | none |

**Verified mouse usage across the fleet (v1.17.17.1): only AG and HFT
features need the physical mouse** (SendInput clicks, first ~2 min of an
AG run). Everything else is synthetic or window-targeted. New integrations
should stay headless unless the app forces it.

### Facts to verify live before writing the feature

1. The real window title — enumerate it, don't guess. Airadio's packaged
   BrowserWindow title is `WestWaveGem` but the page `<title>` wins and the
   actual window is **`ElmWave Network`**. One wrong title = a wasted
   investigate cycle.
2. Whether the window appears for a **packaged exe** (`release\win-unpacked\`
   for electron-builder) vs a dev-mode process (Airadio's `npm run dev` is
   Vite-only — no window at all; only the packaged exe opens one).
3. Rule 1 guard: the attach pattern must match **only** the declared
   window. If the smoke's auto-launch opens a second instance, the feature
   `taskkill`s by the app's own image name before launching (its own
   process, never generic).
4. Chromium windows can paint blank via PrintWindow — the shot's blank gate
   (<8 gray levels) fails honestly; the CDP engine is the fallback.
5. Long-run apps: wait on a window the app itself spawns as completion
   proof (AG: the viewer window at the end of an export) instead of
   asserting text.

### The 4-line skeleton

```python
WINDOW_TITLE = r"^<real title>$"   # verified, not guessed
def run(ctx):
    _kill()                          # reclaim instances this feature owns
    subprocess.Popen([exe], ...)     # launch the packaged exe
    try:
        app = DesktopApp(WINDOW_TITLE, budget_s=ctx.budget_s)
        app.connect()                # WindowNotFoundError -> honest failure
        ctx.desktop = app
        time.sleep(SETTLE_S)         # renderer paints
        ctx.shot("<label>")          # blank-gate checked
    finally:
        _kill()
```

### Long waits (AG pattern, v1.17.17.1)

`wait_for_window(title_pattern, timeout_s, budget_s)` (module-level in
`backend/app/services/desktop_runner.py`) polls for an app-spawned window;
returns `None` on timeout (feature raises `TesterAssertionError`) and
raises on ambiguity. AG: budget 900 s, viewer wait 600 s, settle 4 s —
completion proof without any text read (Rule 3).

## Tier 3 — what stays out (decisions, v1.17.17.1)

- **Tauri: skipped.** WebView2 driveability is unproven and it needs a Rust
  toolchain. Electron (CDP engine, proven since v1.17.14.4) and web
  dashboards are the supported shapes for new desktop apps.
- **AI is never part of a feature** (Rule 3): dinner-menu's Suggest Meal,
  Airadio's Streamlabs-dependent UI, and other LLM/third-party paths are
  intentionally not asserted.
- **Pi-hole stays independent** — Sentinel never starts/stops it.

## Checklist for a new project

1. Index it (watch dirs), confirm discovery + commands on the dashboard.
2. Live-probe: launch, port, auth, window title (enumerate).
3. Write the facts block into the tester/feature docstring.
4. Tier 1 HTTP/CLI tester + registry + tests; or Tier 2 feature.
5. Targeted tests green + lint (black/isort/flake8) + full gate.
6. Restart server, live E2E, verify screenshots non-blank.
7. Changelog row in docs/01/02/03 + this checklist if the pattern changed.