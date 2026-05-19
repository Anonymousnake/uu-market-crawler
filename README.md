# UU Market Crawler Notes

This workspace stores the safe crawler draft for UU Youpin market price checks.

Current status:

- The market page is reachable again at `https://youpin898.com/market`.
- Visible page data includes item name, sale price, sale count, and pagination.
- The real price-list request appears as `querySaleTemplate` in Network.
- A sample `querySaleTemplate` response confirms fields including `commodityName`, `commodityHashName`, `price`, `onSaleCount`, `onLeaseCount`, `leaseUnitPrice`, and `steamPrice`.
- Do not run high-frequency crawling. Start with one request and stop on 403.

Run a one-shot sale template probe:

```powershell
cd D:/Codex/uu-market-crawler
python .\uu_market_probe.py
```

If the sale probe returns `Code=85100`, run it with the logged-in browser headers exported as environment variables. Do not paste those values into chat or commit them to files.

Where to get the required values:

- `authorization`: Chrome DevTools -> Network -> `querySaleTemplate` -> Headers -> Request Headers -> `authorization`
- `UU_UK`: same place, copy `uk`
- `UU_DEVICE_UK`: same place, copy `deviceuk`
- `UU_DEVICE_ID`: same place, copy `deviceid`

You can also edit and run the helper template:

```powershell
notepad D:/Codex/uu-market-crawler/run_with_devtools_headers.ps1
powershell -ExecutionPolicy Bypass -File D:/Codex/uu-market-crawler/run_with_devtools_headers.ps1
```

Faster option: copy the whole DevTools header block to the clipboard and let the helper extract the required values:

```powershell
powershell -ExecutionPolicy Bypass -File D:/Codex/uu-market-crawler/run_from_clipboard_headers.ps1
```

This clipboard helper supports both Chrome formats:

```text
authorization: xxx
```

and:

```text
authorization
xxx
```

The default output is summary mode: status, business code/message, item count, and the first few parsed items. For a full raw response:

```powershell
$env:UU_OUTPUT='raw'
python .\uu_market_probe.py
```

Optional environment variables:

```text
UU_MODE=sale
UU_PAGE_INDEX=1
UU_PAGE_SIZE=20
UU_LIST_SORT_TYPE=0
UU_SORT_TYPE=0
UU_LIMIT=5
UU_OUTPUT=summary
UU_WRITE_CACHE=0
UU_CACHE_DB=D:/Codex/uu-market-crawler/uu_market_cache.sqlite3
UU_HEADERS_FILE=D:/Codex/uu-market-crawler/.secrets/uu_headers.local.json
UU_INCLUDE_RAW_KEYS=0
UU_API_HOST=127.0.0.1
UU_API_PORT=8765
```

Run with saved local headers:

```powershell
cd D:/Codex/uu-market-crawler
$env:UU_HEADERS_FILE='D:/Codex/uu-market-crawler/.secrets/uu_headers.local.json'
$env:UU_MODE='onsale'
$env:UU_TEMPLATE_ID='102276'
$env:UU_WRITE_CACHE='1'
python .\uu_market_probe.py
```

Write parsed results to SQLite:

```powershell
$env:UU_WRITE_CACHE='1'
powershell -ExecutionPolicy Bypass -File D:/Codex/uu-market-crawler/run_from_clipboard_headers.ps1
```

Tables:

```text
sale_template_snapshots
on_sale_listing_snapshots
radar_alert_history
```

Radar notification cooldown:

```text
UU_PUSH_COOLDOWN_HOURS=12
UU_REPUSH_DELTA_EDGE=0.05
```

After a candidate is pushed, the same item is suppressed for the cooldown
window. It can be pushed early again if its edge improves by at least the
configured delta.

Optional Steam history cache:

```text
UU_HISTORY_CACHE_FILE=D:/Codex/uu-market-crawler/steam_history_cache.json
```

Expected shape:

```json
{
  "items": [
    {
      "hash_name": "Recoil Case",
      "volatility_7d": "0.08",
      "volatility_30d": "0.12",
      "volume_24h": 12000,
      "last_price": "3.77"
    }
  ]
}
```

When this file exists, radar risk scoring adds penalties for high 7-day
volatility, recent volatility spikes, and weak 24-hour volume.

Sync Steam-side history from CS2Cap:

```powershell
$env:CS2CAP_API_KEY='...'
$env:UU_HISTORY_CACHE_FILE='D:/Codex/uu-market-crawler/steam_history_cache.json'
python .\cs2cap_history_sync.py
```

The sync uses CS2Cap `/v1/prices` with `providers=steam` and `currency=CNY`,
then builds local daily snapshots. Keep `CS2CAP_HISTORY_LIMIT` conservative on
the free tier; 30 items once per day is about 900 requests per month.

Run the filter/tag probe instead:

```powershell
$env:UU_MODE='filter'; python .\uu_market_probe.py
```

Run the concrete on-sale listing probe for Recoil Case / 反冲武器箱:

```powershell
cd D:/Codex/uu-market-crawler
$env:UU_MODE='onsale'
$env:UU_TEMPLATE_ID='102276'
$env:UU_GAME_ID='730'
$env:UU_LIST_TYPE='10'
python .\uu_market_probe.py
```

With the clipboard helper, set the mode first, then run after copying Request Headers:

```powershell
$env:UU_MODE='onsale'
$env:UU_TEMPLATE_ID='102276'
powershell -ExecutionPolicy Bypass -File D:/Codex/uu-market-crawler/run_from_clipboard_headers.ps1
```

Batch multiple known `templateId` values in one run:

```powershell
$env:UU_MODE='onsale'
$env:UU_TEMPLATE_IDS='102276,PUT_ANOTHER_TEMPLATE_ID_HERE,PUT_THIRD_TEMPLATE_ID_HERE'
$env:UU_LIMIT='3'
powershell -ExecutionPolicy Bypass -File D:/Codex/uu-market-crawler/run_from_clipboard_headers.ps1
```

Batch mode waits a random 3-10 seconds between items and stops immediately on `403` or `429`.
That means the workflow can be automated once the target box/capsule `templateId` list is known.

Discover bulk case/capsule template ids from the logged-in DevTools browser:

```powershell
node D:/Codex/uu-market-crawler/discover_watchlist_from_devtools.js
```

Outputs:

```text
D:/Codex/uu-market-crawler/watchlist.discovered.json
D:/Codex/uu-market-crawler/watchlist.discovered.csv
D:/Codex/uu-market-crawler/watchlist.json
```

Run the on-sale probe from the discovered watchlist:

```powershell
$env:UU_MODE='onsale'
$env:UU_WATCHLIST_FILE='D:/Codex/uu-market-crawler/watchlist.json'
$env:UU_LIMIT='1'
powershell -ExecutionPolicy Bypass -File D:/Codex/uu-market-crawler/run_from_clipboard_headers.ps1
```

Observed single-item page:

```text
https://youpin898.com/market/goods-list?listType=10&templateId=102276&gameId=730
```

Observed on-sale listing endpoint:

```text
POST https://api.youpin898.com/api/homepage/pc/goods/market/queryOnSaleCommodityList
```

Observed `queryOnSaleCommodityList` payload:

```json
{
  "gameId": "730",
  "listType": "10",
  "templateId": "102276",
  "listSortType": 1,
  "sortType": 0,
  "pageIndex": 1,
  "pageSize": 10
}
```

Observed response fields for each listing include:

```text
id
commodityNo
commodityName
commodityHashName
templateId
price
typeName
storeName
userNickName
publishTime
leaseUnitPrice
longLeaseUnitPrice
leaseDeposit
iconUrl
iconUrlLarge
```

Next manual evidence needed only if the endpoint changes:

1. In Chrome DevTools, open Network -> Fetch/XHR.
2. Click the successful `querySaleTemplate` request.
3. Copy only:
   - Request URL
   - Request Payload
   - Response JSON first 1-2 records if the schema changes
   - Status code
4. Do not paste full Cookie, authorization, uk, deviceUk, deviceId, or acw_tc values.
