# TP DECK

**Techyparts Data Entry Control Keeper** — a native Windows multimonitor utility that automates repetitive inventory data entry between eBay Seller Hub orders and an internal ERP, without launching new browser windows.

## How it works (v1)

1. You already have Chrome running with remote debugging enabled (CDP).
2. TP DECK opens as a compact, always-on-top floating panel and remembers its position across monitors.
3. You click **Execute**. Playwright attaches to your existing Chrome session(s) over CDP — it never calls `launch()`.
4. The engine finds the focused eBay order tab (`ebay.com/sh/ord` + `document.hasFocus()`), scrapes the order/SKU.
5. It locates the ERP tab, enters the SKU, scrapes the Location, builds  
   `[Order] - [SKU] - [Location]`, and copies that string to the clipboard.
6. While a run is active, **Execute** shows `⏳ Processing...` and is disabled. Status moves Idle → Processing → Success.
7. At any time, **🛑 Emergency Stop** or the global **Pause** key cancels the in-flight `asyncio` task immediately.

### Connection modes (`settings.json`)

| Mode | Behavior |
|------|----------|
| **Single** | One Chrome instance on the eBay CDP port (default `9222`) |
| **Dual** | eBay on `9222`, ERP on `9223` |

All ports, CSS selectors, hotkeys, and window geometry live in `tp_deck/settings.json` — nothing is hardcoded.

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

Open your eBay Seller Hub order tab and ERP tab in those profiles, then run TP DECK.

## Tech stack

- **Python** 3.11+
- **PySide6** — floating tool window (`WindowStaysOnTopHint | Tool`)
- **Playwright** (async) — `connect_over_cdp` only
- **keyboard** — Pause/Break emergency abort only (no broad keyhooks)
- **asyncio** — cancelable background automation tasks

## Packaging note

The shipped app will use Windows Embedded Python (`.zip`) launched via a `.bat` script — not PyInstaller `--onefile`. Local development is simply:

```bat
python main.py
```

## Project layout

```
TP-DECK/
├── main.py                 # Root launcher
├── requirements.txt
└── tp_deck/
    ├── main.py             # Entry point, event loop, hotkey registration
    ├── dashboard.py        # Floating UI, theme, position persistence
    ├── settings_dialog.py  # Single/Dual mode, ports, setup help
    ├── automation_engine.py# Playwright CDP connect + DOM extraction
    ├── settings.json       # Ports, selectors, window_x/y, mode
    └── tpdeck.log          # Rolling local log
```

## Development status

| Phase | Scope | Status |
|-------|--------|--------|
| **1** | Floating shell, QSS theme, position persistence, Settings dialog | Done |
| **2** | asyncio ↔ PySide6 bridge, dummy task, Pause + Stop cancel | Next |
| **3** | `connect_over_cdp`, focused-tab disambiguation | Planned |
| **4** | eBay/ERP scrape, clipboard output `[Order] - [SKU] - [Location]` | Planned |

## Local run (Phase 1)

```bat
pip install -r requirements.txt
python main.py
```

You should see the dark floating panel. Use **⚙** for Single/Dual and ports. Execute/Stop are UI stubs until Phase 2.

## Theme

Techyparts enterprise hardware dark theme:

| Role | Color |
|------|--------|
| Background | Deep Server Charcoal `#0F172A` / `#1E293B` |
| Accent | Enterprise Electric Blue `#0EA5E9` |
| Text | Crisp White `#F8FAFC` |
| Emergency Stop | `#7F1D1D` bg / `#FCA5A5` text |
