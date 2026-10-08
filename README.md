# TP DECK

Techyparts Data Entry Control Keeper. A Windows desktop panel that copies order lines from eBay Seller Hub (and ShipStation store orders) into RazorERP locations, builds a pick list, and types batch serial numbers. It keeps its own Chromium window and attaches to it. It does not open a new browser for each run.

## Using TP DECK

1. Unzip the release anywhere, for example `C:\Apps\TP-DECK`.
2. Double-click `TP-DECK.bat`. The first open downloads Chromium (network required once) and starts it minimized. Later opens restore the last tabs. If Windows SmartScreen appears, choose **More info → Run anyway**.
3. In that Chromium window, sign in once to Seller Hub orders (`ebay.com/sh/ord`), Razor inventory, and the ShipStation awaiting-shipment page.
4. Use the panel:

| Control | What it does |
| --- | --- |
| **Scrape eBay Orders** | Copies `Order - Buyer - SKU - Location` |
| **Auto-cycle** | Repeats the scrape on a timer and fills the location cache. The ring on the button counts down the wait |
| **Generate Pick List** | Copies a walk-sorted quantity list |
| **Batch Serial** | Types serial numbers into a Razor sales order. Paste them while a cycle is running; they start when that pass finishes |
| **Emergency Stop** or **Pause** | Cancels the current task immediately |
| **Power** | Safe Shutdown. The panel fades to grey for 5 seconds. Emergency Stop during that fade cancels it. Otherwise TP DECK finishes the current task, closes extra sales-order tabs, and closes Chromium |

The lamp is green while something is running, pulses blue while auto-cycle is waiting, and is red when nothing is running or queued.

A later open of a release install downloads a newer GitHub release when one is published. The browser profile, settings, SKU cache, and sister-SKU list stay in place.

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
- Playwright attaches with `connect_over_cdp` only
- The Pause key is the only global hotkey

Build the coworker zip:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1
```

Publish by bumping `__version__` in `tp_deck/__init__.py`, pushing `main`, then pushing a tag that is `v` plus that version (for 0.2.8, the tag is `v0.2.8`). GitHub Actions builds the zip and attaches it to the release. Installed copies pick that zip up the next time they open.

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
