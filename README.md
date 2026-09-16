# TP DECK

**Techyparts Data Entry Control Keeper** — a native Windows multimonitor utility that automates repetitive inventory data entry between eBay Seller Hub orders and an internal ERP, without launching new browser windows.

## How it works (v1)

1. You already have Chrome running with remote debugging enabled (CDP).
2. TP DECK opens as a compact, always-on-top floating panel and remembers its position across monitors.
3. You click **Scrape eBay Orders** or **Generate Pick List**. Playwright attaches to your existing Chrome session(s) over CDP — it never calls `launch()`.
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
| **Single** | One Chrome instance on the eBay CDP port (default `9222`); set `erp_url_pattern` so the ERP tab can be found in the same browser |
| **Dual** | eBay on `9222`, ERP on `9223` |

All ports, CSS selectors, URL patterns, timeouts, hotkeys, and window geometry live in `tp_deck/settings.json` (also editable in **⚙ Settings**).

### Chrome CDP setup

**Single mode:**
```bat
chrome.exe --remote-debugging-port=9222 --user-data-dir="%TEMP%\tpdeck-chrome"
```

**Dual mode:**
```bat
chrome.exe --remote-debugging-port=9222 --user-data-dir="%TEMP%\tpdeck-ebay"
chrome.exe --remote-debugging-port=9223 --user-data-dir="%TEMP%\tpdeck-erp"
```

Open your eBay Seller Hub order tab and ERP tab in those profiles, fill selectors in Settings, then run TP DECK.

## Tech stack

- **Python** 3.11+
- **PySide6** — floating tool window (`WindowStaysOnTopHint | Tool`)
- **Playwright** (async) — `connect_over_cdp` only
- **keyboard** — Pause/Break emergency abort only (no broad keyhooks)
- **qasync** — shared Qt + asyncio event loop
- **asyncio** — cancelable background automation tasks

## Packaging note

The shipped app will use Windows Embedded Python (`.zip`) launched via a `.bat` script — not PyInstaller `--onefile`. Local development:

```bat
TP-DECK.bat
```

## Project layout

```
TP-DECK/
├── main.py                 # Root launcher
├── TP-DECK.bat             # Launch without leaving a console open
├── requirements.txt
└── tp_deck/
    ├── main.py             # Entry point, event loop, hotkey registration
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

**v1 check:** Chrome with CDP → focused eBay order tab → selectors configured → Execute → clipboard gets `Order - SKU - Location`. Pause / Emergency Stop cancels mid-run.

## Theme

Techyparts enterprise hardware dark theme:

| Role | Color |
|------|--------|
| Background | Deep Server Charcoal `#0F172A` / `#1E293B` |
| Accent | Enterprise Electric Blue `#0EA5E9` |
| Text | Crisp White `#F8FAFC` |
| Emergency Stop | `#7F1D1D` bg / `#FCA5A5` text |
