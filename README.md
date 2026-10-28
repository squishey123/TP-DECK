# TP DECK

Techyparts Data Entry Control Keeper. A Windows desktop panel that copies order lines from eBay Seller Hub (and ShipStation store orders) into RazorERP locations, builds a pick list, and types batch serial numbers. It keeps its own Chromium window and attaches to it. It does not open a new browser for each run.

## Using TP DECK

1. Unzip the release anywhere, for example `C:\Apps\TP-DECK`.
2. Double-click `TP-DECK.bat`. The first open downloads Chromium (network required once) and starts it minimized. Later opens restore the last tabs. If Windows SmartScreen appears, choose **More info → Run anyway**.
3. In that Chromium window, sign in once to Seller Hub orders (`ebay.com/sh/ord`), Razor inventory, and the ShipStation awaiting-shipment page.
4. Use the panel:

| Control | What it does |
| --- | --- |
| **Scrape eBay Orders** | Copies `Order - Buyer - SKU - Location`. Also reads store orders from the open ShipStation awaiting-shipment tab |
| **Auto-cycle** | Repeats that lookup on a timer and only fills the location cache. The ring on the button counts down the wait. ShipStation store orders sync every few passes. Batch Serial stays available during a pass and runs when that pass finishes |
| **Generate Pick List** | Copies a walk-sorted quantity list. Also reads the open ShipStation awaiting-shipment tab |
| **Batch Serial** | Types serial numbers into a Razor sales order. `SO-12345` searches the sales order number. An eBay order number searches the eBay order column, not Source Type. A digits-only store order searches that order number, then the sales order number. A blank number uses the one open order tab. Paste serials while a cycle is running; they start when that pass finishes |
| **Tools** (toolbox button) | Opens the tools menu. **Uline Order Builder** — type a Uline Item # (`S-11633`) or a saved alias, and TP DECK fills in the full name, price, price tiers, unit of sale, and units per set, then works out how many packs to buy for the units you need (Needed Units ÷ Units Per Set, rounded up, never below Uline's minimum pack). Pages that price a smaller item (boxes, rolls) are converted to the pack Uline sells in (Bundle, Case), so a bundle of 25 boxes at $0.80 per box shows as $20 per Bundle. Items are cached forever in `tp_deck/uline_cache.json`, and the Cached Items tab edits, re-checks, aliases, and deletes them. Typing a price in the Price column overrides the tier price; type 0 to clear it. Export writes CSV and optional PDF into the export folder set in Settings, default `Uline Exports` beside `TP-DECK.bat`. The draft order is kept between opens, and lookups keep the TP DECK Chromium window minimized |
| **Emergency Stop** or **Pause** | Cancels the current task immediately. During the shutdown fade, Emergency Stop cancels shutdown |
| **Power** | Safe Shutdown. The panel fades to grey for 5 seconds. Emergency Stop during that fade cancels it. Otherwise TP DECK finishes the current task, closes extra sales-order tabs, and closes Chromium |

The lamp is green while something is running, pulses blue while auto-cycle is waiting, and is red when nothing is running or queued.

A later open of a release install checks for a newer GitHub release. The status shows the version and the day it was released, and the action buttons pulse between yellow and grey. Emergency Stop cancels that update until the next launch. A bar shows download progress. The browser profile, settings, SKU cache, and sister-SKU list stay in place.

Ports, timeouts, and window position are in **Settings**. CSS selectors are in `tp_deck/locators.json`.

## Development

Python 3.11+. From the repo root:

```bat
pip install -r requirements.txt
TP-DECK.bat
```

`TPDECK_DEBUG=1` keeps a console open. Do not package with PyInstaller `--onefile`.

- `tp_deck/settings.json` — ports, mode, timers, window position
- `tp_deck/locators.json` — CSS selectors and Razor addresses
- `tp_deck/uline_cache.json` — permanent Uline items, prices, units per set, aliases
- `tp_deck/uline_draft.json` — the Uline order being built
- Uline exports (CSV/PDF) go to the export folder in Settings, default `Uline Exports` beside `TP-DECK.bat`
- Playwright attaches with `connect_over_cdp` only
- The Pause key is the only global hotkey

Build the coworker zip:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1
```

Publish by pushing a tag such as `v0.3.2`. GitHub Actions reads that tag, stamps it into the zip, and attaches the zip to the release. Wait until that workflow finishes before editing the release notes; the create-release step overwrites the body when the zip is attached. The window shows the latest `v*` tag in a git checkout, and the stamped version in an installed copy. Installed copies pick up the new zip the next time they open.

```
TP-DECK/
├── main.py
├── TP-DECK.bat
├── requirements.txt
├── scripts/build_release.ps1
└── tp_deck/
    ├── main.py
    ├── automation_engine.py
    ├── chrome_launcher.py
    ├── dashboard.py
    ├── sales_order.py
    ├── serial_dialog.py
    ├── shipstation.py
    ├── updater.py
    ├── locators.json
    └── settings.json
```
