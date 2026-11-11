# TP DECK

Techyparts Data Entry Control Keeper. A Windows desktop panel that copies order lines from eBay Seller Hub (and ShipStation store orders) into RazorERP locations, builds a pick list, and types batch serial numbers. It keeps one Chrome window open and attaches to it for every run.

## Using TP DECK

1. Unzip the release anywhere, for example `C:\Apps\TP-DECK`.
2. Double-click `TP-DECK.bat`. Each open checks for a newer Chrome and downloads it when the installed copy is older (network required for that download), then starts it minimized. Later opens restore the last tabs. Logins live in `chrome-profiles` beside the app. If Windows SmartScreen appears, choose **More info → Run anyway**.
3. In that Chrome window, sign in once to Seller Hub orders (`ebay.com/sh/ord`), Razor inventory, and the ShipStation awaiting-shipment page. Open Uline in the same window when you use the order builder.
4. Use the panel:

| Control | What it does |
| --- | --- |
| **Scrape eBay Orders** | Copies `Order - Buyer - SKU - Location`. Also reads store orders from the open ShipStation awaiting-shipment tab |
| **Auto-cycle** | Repeats that lookup on a timer and only fills the location cache. The ring on the button counts down the wait. ShipStation store orders sync every few passes. Batch Serial stays available during a pass and runs when that pass finishes |
| **Generate Pick List** | Copies a walk-sorted quantity list. Also reads the open ShipStation awaiting-shipment tab |
| **Batch Serial** | Types serial numbers into a Razor sales order. **Current Orders** lists the orders from the last scrape, including an auto-cycle pass. Double-click one order to fill the order box. Tick several orders and the window stays open: each submit queues that order's own serials, clears the box, and shows the next order. The last submit closes the window. `SO-12345` searches the SO # column. An eBay order number and a digits-only store order search the PO column. A pasted `#` in front of a store number is ignored. A blank number uses the one open order tab. Paste serials while a cycle is running; they start when that pass finishes |
| **Tools** (toolbox button) | Opens the tools menu. **Uline Order Builder** — type a Uline Item # (`S-11633`, `H-64B`, `S-4307-2`) or a saved alias — letters after the number stay glued to it and a dash group too (`H-64B`, `S-4307-2`), so `H-64B` and `h-64 b` both mean H-64B and never the different item H-64, while a number whose digits run past six is not an item number at all and is searched as words instead of guessed at — and TP DECK fills in the full name, price, price tiers, unit of sale, and units per set, then works out how many packs to buy for the units you need (Needed Units ÷ Units Per Set, rounded up, never below Uline's minimum pack). Pages that price a smaller item (boxes, rolls) are converted to the pack Uline sells in (Bundle, Case), so a bundle of 25 boxes at $0.80 per box shows as $20 per Bundle. A header such as `PRICE EACH(MIN. 12)` raises that line to 12 when each unit is one pack. A minimum stated in rolls or boxes becomes whole packs, so 18 rolls in a 36-roll case is 1 case. **Lazy**, beside Add, records what you typed without opening Uline. Turning Lazy off opens one tab per waiting line and fills them in together. Items are cached forever in `tp_deck/uline_cache.json`, and the Cached Items tab edits, re-checks, aliases, and deletes them. Typing a price in the Price column overrides the tier price; type 0 to clear it. Export writes CSV and optional PDF into the export folder set in Settings, default `Uline Exports` beside `TP-DECK.bat`. The draft order is kept between opens, and lookups keep the TP DECK Chrome window minimized |
| **Emergency Stop** or **Pause** | Cancels the current task immediately. During the shutdown fade, Emergency Stop cancels shutdown |
| **Power** | Safe Shutdown. The panel fades to grey for 5 seconds. Emergency Stop during that fade cancels it. Otherwise TP DECK finishes the current task, closes extra sales-order tabs, and closes Chrome |

The lamp is green while something is running, purple while the title lamp is forcing a refresh, pulses blue while auto-cycle is waiting, and is red when nothing is running or queued.

A later open checks for a newer Chrome. A release install also checks for a newer GitHub release. The status names the update. An app update pulses the action buttons between yellow and grey. A Chrome update pulses them between green and grey. When both are ready they alternate yellow and green every half second. Emergency Stop cancels that update until the next launch. A bar shows download progress. The app update installs first and closes TP DECK; Chrome updates on the next open. Logins stay in `chrome-profiles`. Settings, the SKU cache, and the sister-SKU list stay in place.

**Settings** has three tabs: Connection & Scrape, Uline Builder, and Appearance. Appearance chooses the theme. **Mobile Mode** (laptop) draws the panel as one wide, low strip: the buttons become squares with symbols and their tooltips still name them, and the title, lamp and status readout shrink to fit. The panel is sized when TP DECK opens, so switch Mobile Mode on or off and open TP DECK again to change its shape. **Fade Out main UI** fades the open panel to see-through after five idle seconds, and it comes back solid when you move the pointer onto it or click it; Safe Shutdown and an update warning hold it solid while they are waiting for your click, and a run or the auto-cycle do not. Cyan and Neutral keep the original amber cycle button. Every other theme draws its cycle button, warnings, and update pulse from its own accent. Ports, timeouts, and window position are on Connection & Scrape. CSS selectors are in `tp_deck/locators.json`.

## Development

Python 3.11+. From the repo root:

```bat
pip install -r requirements.txt
TP-DECK.bat
```

`TPDECK_DEBUG=1` keeps a console open. Do not package with PyInstaller `--onefile`.

- `tp_deck/settings.json` — ports, mode, theme, timers, window position, and the saved `chrome_profile_dir`
- `tp_deck/locators.json` — CSS selectors and Razor addresses
- `tp_deck/uline_cache.json` — permanent Uline items, prices, units per set, aliases
- `tp_deck/uline_draft.json` — the Uline order being built
- `chrome-profiles/` — Chrome logins, beside the app. App updates and Chrome updates leave this folder in place
- `browser/` — the Chrome for Testing build. Profiles do not live here
- Uline exports (CSV/PDF) go to the export folder in Settings, default `Uline Exports` beside `TP-DECK.bat`
- Playwright attaches with `connect_over_cdp` only
- The Pause key is the only global hotkey

Build the coworker zip:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1
```

Publish by pushing a tag such as `v0.4.0`. GitHub Actions reads that tag, stamps it into the zip, and attaches the zip to the release. Wait until that workflow finishes before editing the release notes; the create-release step overwrites the body when the zip is attached. The window shows the latest `v*` tag in a git checkout, and the stamped version in an installed copy. Installed copies pick up the new zip the next time they open. A commit on `main` updates this README without a new tag.

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
    ├── settings_dialog.py
    ├── theme.py
    ├── sales_order.py
    ├── serial_dialog.py
    ├── shipstation.py
    ├── uline_dialog.py
    ├── uline_scraper.py
    ├── updater.py
    ├── locators.json
    └── settings.json
```
