# TP DECK

**Techyparts Data Entry Control Keeper** — a native Windows multimonitor utility that automates repetitive inventory data entry between eBay Seller Hub orders and an internal ERP. Opening the app starts a separate Chromium window; each scrape attaches to that window.

## How it works (v1)

1. Open TP DECK. It starts Playwright's Chromium minimized, with remote debugging, and restores the last session. The first open downloads Chromium into a `browser` folder next to the app (network required once). If the debug port is already open, that window is left where you put it.
2. TP DECK opens as a compact, always-on-top floating panel and remembers its position across monitors.
3. You click **Scrape eBay Orders** or **Generate Pick List**. Playwright attaches to that Chromium session over CDP — it never calls `launch()`.
4. The engine finds the focused eBay order tab (`ebay.com/sh/ord` + `document.hasFocus()`), scrapes Order, buyer, SKU, and quantity via CSS selectors from `settings.json`.
5. It locates the ERP tab, enters each SKU, waits for Location, then copies either  
   `[Order] - [Buyer] - [SKU] - [Location]` (scrape) or a walk-sorted pick list  
   `Nx - SKU - LOCATION` grouped CPU Rack → Cubbies → Endcaps → Small parts → Cabinet → Misc.  
   SKU → location results are cached for 12 hours when **Cache SKU → location** is enabled in Settings (`cache_enabled` / `cache_ttl_hours`). Turn that off when bins are moving often.
6. While a run is active, both action buttons disable and the active one shows `⏳ Processing...`. Status moves Idle → Processing → Success.
7. At any time, **🛑 Emergency Stop** or the global **Pause** key cancels the in-flight `asyncio` task immediately.

### Connection modes (`settings.json`)

| Mode | Behavior |
|------|----------|
| **Single** | One Chromium window on the eBay CDP port (default `9222`); set `erp_url_pattern` so the ERP tab can be found in the same browser |
| **Dual** | eBay on `9222`, ERP on `9223` (two Chromium windows) |

All ports, CSS selectors, URL patterns, timeouts, hotkeys, and window geometry live in `tp_deck/settings.json` (also editable in **⚙ Settings**). `chrome_version` records the Playwright Chromium revision after the first install.

### Chromium

Opening TP DECK is the daily start. Chromium stays minimized and restores yesterday's eBay and ERP tabs. Log into those sites in that window once. Profiles live in `browser/profile` (and `browser/profile-erp` in dual mode), separate from installed Chrome.

If automatic start fails, launch a browser yourself:

**Single mode:**
```bat
chrome.exe --remote-debugging-port=9222 --user-data-dir="%TEMP%\tpdeck-chrome"
```

**Dual mode:**
```bat
chrome.exe --remote-debugging-port=9222 --user-data-dir="%TEMP%\tpdeck-ebay"
chrome.exe --remote-debugging-port=9223 --user-data-dir="%TEMP%\tpdeck-erp"
```

Then focus the eBay order tab and run TP DECK again.

## Tech stack

- **Python** 3.11+
- **PySide6** — floating tool window (`WindowStaysOnTopHint | Tool`)
- **Playwright** (async) — `connect_over_cdp` only
- **keyboard** — Pause/Break emergency abort only (no broad keyhooks)
- **qasync** — shared Qt + asyncio event loop
- **asyncio** — cancelable background automation tasks

## Packaging / coworker zip

Do **not** use PyInstaller `--onefile`. Defender often flags those. Releases are official **python.org embeddable Python** plus the `.py` files, started with `TP-DECK.bat`.

### Build the zip (on this machine)

From the repo root in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1
```

Output: `dist\TP-DECK-0.2.2-windows-x64.zip`

Coworkers unzip it and double-click `TP-DECK.bat`. No Python install required.

### Publish a GitHub Release (recommended)

Pushing a version tag runs `.github/workflows/release.yml` on Windows: it builds the zip and creates the GitHub Release with the asset attached.

1. Bump `tp_deck/__init__.py` (e.g. `0.2.3`), commit, and push `main`.
2. Tag that commit and push the tag (tag must match `v` + `__version__`):

```powershell
git tag -a v0.2.3 -m "TP DECK 0.2.3"
git push origin v0.2.3
```

3. Watch **Actions → Release**. When it finishes, the zip is on the **Releases** page.

### Publish locally (optional)

If you need a zip without GitHub Actions:

1. Build with `scripts\build_release.ps1` (above).
2. Tag and upload with `gh`:

```powershell
git tag -a v0.2.2 -m "TP DECK 0.2.2"
git push origin v0.2.2
gh release create v0.2.2 "dist/TP-DECK-0.2.2-windows-x64.zip" --title "TP DECK 0.2.2" --notes "Embeddable Python zip. Unzip and run TP-DECK.bat."
```

Send coworkers the **Release** page or an internal copy of the zip (internal share is less likely to trip SmartScreen than a random email attachment).

If Windows shows “unrecognized app”, that is SmartScreen reputation on a new file, not a packed exe. Prefer **More info → Run anyway** after IT is aware, or host the zip on SharePoint/a file share.

## Project layout

```
TP-DECK/
├── main.py                 # Root launcher
├── TP-DECK.bat             # Launch without leaving a console open
├── requirements.txt
└── tp_deck/
    ├── main.py             # Entry point, event loop, hotkey registration
    ├── chrome_launcher.py  # Install and start minimized Chromium on open
    ├── dashboard.py        # Floating UI, theme, position persistence
    ├── settings_dialog.py  # Mode, ports, patterns, selectors, setup help
    ├── automation_engine.py# CDP connect, tab focus, scrape, clipboard
    ├── settings.json       # Ports, selectors, window_x/y, mode
    └── tpdeck.log          # Rolling local log
```

## Development status

| Phase | Scope | Status |
|-------|--------|--------|
| **1** | Floating shell, QSS theme, position persistence, Settings dialog | Done |
| **2** | asyncio ↔ PySide6 bridge, dummy task, Pause + Stop cancel | Done |
| **3** | `connect_over_cdp`, focused-tab disambiguation | Done |
| **4** | eBay/ERP scrape, clipboard output `[Order] - [Buyer] - [SKU] - [Location]` | Done |

## Local run

```bat
pip install -r requirements.txt
TP-DECK.bat
```

Or `pythonw main.py`. Set `TPDECK_DEBUG=1` to keep a console for live logs.

**v1 check:** Open TP DECK (Chromium starts minimized) → focused eBay order tab → selectors configured → Execute → clipboard gets `Order - SKU - Location`. Pause / Emergency Stop cancels mid-run.

## Theme

Techyparts enterprise hardware dark theme:

| Role | Color |
|------|--------|
| Background | Deep Server Charcoal `#0F172A` / `#1E293B` |
| Accent | Enterprise Electric Blue `#0EA5E9` |
| Text | Crisp White `#F8FAFC` |
| Emergency Stop | `#7F1D1D` bg / `#FCA5A5` text |
