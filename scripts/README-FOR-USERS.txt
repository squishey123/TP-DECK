TP DECK — how to run (no Python install needed)

1. Unzip this folder anywhere (example: C:\Apps\TP-DECK).
2. Double-click TP-DECK.bat. The first open downloads Chromium (needs network once) and starts it minimized. Later opens restore the last tabs. If a newer TP DECK has been published, that open installs it and keeps your logins and settings.
3. In that Chromium window, open Seller Hub orders (ebay.com/sh/ord), RazorERP Inventory Detail, and ShipStation awaiting shipment. You only need to log in once.
4. Scrape eBay Orders copies order lines. Generate Pick List copies a walk-sorted list. Batch Serial types serial numbers into a sales order. The cycle button repeats the scrape on a timer.

Emergency stop: the red button, or the Pause/Break key. That cancels immediately.

Safe shutdown: the power button. The panel fades to grey for 5 seconds. Press Emergency Stop during that fade to cancel. Otherwise TP DECK finishes the current task, closes extra order tabs, and closes Chromium.

Tools: the toolbox button next to Settings opens the tools menu. Uline Order Builder
is the tool: type a Uline item number (S-11633) or an alias you saved, set the units
you need, and TP DECK works out how many packs to buy from the units per set. When
Uline prices a smaller item (a box, a roll), TP DECK converts to the pack it sells in
(a Bundle of 25 boxes, a Case of 36 rolls), so the price shown is the price per pack,
and packs are never below Uline's minimum. Typing a price in the Price column overrides
the tier price; type 0 to clear it. Export saves CSV (and optional PDF) in the export
folder you pick in Settings; the default is the "Uline Exports" folder beside
TP-DECK.bat. Items and aliases are remembered. Open the Uline site in the TP DECK
Chromium window so lookups reuse your login and that tab; lookups put the window back
to minimized if it was minimized.

This package uses official Python from python.org plus the TP DECK scripts.
It is not a packed .exe (those often get blocked by Windows Defender).

First SmartScreen prompt (if any): More info → Run anyway.
