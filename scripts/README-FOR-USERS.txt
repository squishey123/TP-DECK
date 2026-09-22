TP DECK — how to run (no Python install needed)

1. Unzip this folder anywhere (example: C:\Apps\TP-DECK).
2. Start Chrome with remote debugging (PowerShell):

   & "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="$env:TEMP\tpdeck-chrome"

3. In that Chrome, open Seller Hub orders (ebay.com/sh/ord) and RazorERP Inventory Detail.
4. Double-click TP-DECK.bat
5. Click "Scrape eBay Orders" or "Generate Pick List"

Emergency stop: red button, or the Pause/Break key.

This package uses official Python from python.org plus the TP DECK scripts.
It is not a packed .exe (those often get blocked by Windows Defender).

First SmartScreen prompt (if any): More info → Run anyway. Prefer copying from
an internal share instead of downloading the zip from a random email.
