# Plan: Log Viewer page in the web UI

## Context
openeo's text logs can't be viewed from the web UI:
- **App event log:** a SQLite `log` table, purged after 24h by default. Written by `configDB.logwrite()`.
- **Python `logging`:** goes to syslog, then the systemd journal, which is persistent.

Today you can only see them through the unauthenticated `/debugdata` dump, the unlinked `/static/debug.html` page, or SSH. The new **Log Viewer** page will sit in the React UI menu and show both sources in tabs. It will have a line-limit selector, a text filter, auto-refresh with tail-follow, and download-as-.txt.

**Isolation from the BSSID work:** the work goes in its own worktree and branch, `log-viewer`, created from `main`. The BSSID pinning work (worktree `spec-bssid-selection-quality`) only touches `portal/*`, `tests/test_wifi_pinning.py` and its spec doc. This plan touches none of those files, so neither branch should conflict with the other.

## Setup
- `EnterWorktree name=log-viewer`. This creates branch `worktree-log-viewer` from `origin/main`.
- Commit the spec and plan under `docs/superpowers/` on that branch only.

## Backend

### 1. `openeoConfig.py`: `logget()` (around line 28)
- Add an optional `limit=None` parameter.
- When `limit` is set, return the newest N rows in chronological order: `SELECT * FROM (SELECT * FROM log ORDER BY timestamp DESC LIMIT ?) ORDER BY timestamp ASC`.
- Keep the default behaviour as it is, so `/debugdata` doesn't change.

### 2. `lib/configserver.py`: new `GET /getlogs` endpoint
Add it next to the `/debugdata` block (around line 190), before the static-file fallback.
- **Query parameters:**
  - `source=app|journal`, default `app`.
  - `lines=N`, clamped to 50–5000, default 500.
- **Parsing:** use `urllib.parse.parse_qs`, which is already imported and used for `/getchartdata`. Put the parsing and clamping in a small pure function, `_parse_log_query(path) -> (source, lines)`, so it can be unit-tested.
- **`app` source:** `globalState.configDB.logget(limit=lines)`.
- **`journal` source:** `subprocess.run(["journalctl", "-u", "openeo", "-n", str(lines), "--no-pager", "-o", "short-iso"], capture_output=True, text=True, timeout=10)`.
  - Pass the arguments as a list with no `shell=True`, so query values never reach a shell.
  - On error or timeout, return the error text as the content.
  - `/debugdata` already runs `journalctl` as user `pi`, so permissions are known to work.
- **Response:** `200` with JSON `{"source": ..., "lines": ..., "content": "<text>"}`.
  - Use the existing `self.send_response` / `send_header` / `end_headers` pattern.
  - Copy the dev-mode CORS headers from the `/getconfig` block, around line 95.
- **Stop logging the polling:** at line 108, skip `logwrite(f"page GET request:...")` when the path starts with `/getlogs`. Otherwise every auto-refresh would add rows to the log being viewed.

## Frontend (`openeo-ui/`)

### 3. New `openeo-ui/src/openeo-LogViewer.jsx`
Base the structure and styling on `openeo-Update.jsx`:
- `styles.page`, `styles.pageTitle` (`// System Logs`), `styles.section`, `styles.Btn` and `globalCss` from `./utils/styles`.
- `buildUrl()` from `./utils/funcs`.
- `useToastContext` for errors.
- The readonly log textarea from Update.

**Controls:**
- **Tabs:** "App Events" (`source=app`) and "Service Journal" (`source=journal`).
- **Lines selector:** 200 / 500 / 1000 / 5000.
- **Filter box:** case-insensitive substring match, applied client-side to lines.
- **Auto-refresh toggle:** 5 s `setInterval`, cleared on unmount and when switched off, the same way `timerRef` is handled in Update.
- **Tail-follow:** stay scrolled to the bottom unless the user has scrolled up. Reuse the `outputRef.scrollTop = scrollHeight` effect from Update.
- **Download:** build a `Blob` of the visible text and save it as `openeo-<source>-<timestamp>.txt`.
- **Refresh button.**

### 4. `openeo-ui/src/main.jsx`
- Import `LogViewer`.
- Add `case "logs": return <LogViewer />;`.
- Add a menu entry `{ label: "System Logs", href: "?r=logs" }` after "Charging Log".

### 5. Rebuild the bundled UI
The repo commits a prebuilt bundle, `lib/configserver/static/ui.js` and `ui.css`; commit `9105465` shows the pattern. Steps:
- Run `npm --prefix openeo-ui ci && npm --prefix openeo-ui run build`.
- Copy the generated `dist/assets/*.js` to `lib/configserver/static/ui.js` and `dist/assets/*.css` to `ui.css`.
- Leave `static/index.html` unchanged.

## Docs and tests
- **`docs/API.md`:** add a `GET /getlogs` section next to `/debugdata` (around line 351), with parameters, an example and limits.
- **`CHANGELOG.md`:** add an "Added: System Logs viewer page" entry.
- **New `tests/test_getlogs.py`:** unit-test `_parse_log_query` for defaults, clamping (0 → 50, 99999 → 5000), non-numeric `lines`, and an unknown `source` falling back to `app`. Also test `logget(limit=)` against a temporary-file SQLite `openeoConfig`, if the constructor allows it; otherwise test that SQL with a stub table.

## Out of scope (note in PR)
- Authentication: no endpoint in the web UI has it.
- Trimming the unbounded `journalctl` in `/debugdata`.
- Fixing the `WARNING` level override and the unattached console handler in `openeo.py`.
- Level, time-range and boot filtering (the "advanced" tier).

## Verification
1. `python -m pytest tests/test_getlogs.py`.
2. `npm --prefix openeo-ui run lint` and `npm --prefix openeo-ui run build`.
3. Local backend smoke test: run `openeo.py`, or exercise just the handler if no charger hardware is present.
   - `curl 'http://localhost/getlogs?source=app&lines=50' | jq .` should return the newest 50 rows, oldest first.
   - `curl 'http://localhost/getlogs?source=journal&lines=50'` should return journal text, or an error string on a non-systemd dev box.
   - `curl '.../getlogs?lines=abc'` should fall back to 500 lines.
4. UI in `npm run dev`, which points at a device via `buildUrl` (`192.168.123.50`):
   - Open `?r=logs` and switch tabs.
   - Type a filter.
   - Toggle auto-refresh and confirm tail-follow and that the interval stops on navigation.
   - Download the .txt.
   - Confirm `/getlogs` polls don't appear in the App Events tab.
5. `git diff --stat main` lists no `portal/*` files, which confirms isolation from the BSSID branch.
6. Commit on `worktree-log-viewer`, push, and open a draft PR.
