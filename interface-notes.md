# UU Youpin Interface Notes

## Confirmed Base

```text
https://api.youpin898.com
```

## Market Filter Endpoint

```http
POST https://api.youpin898.com/api/youpin/commodity/v2/commodity/tag/query/list
```

Observed behavior:

- Request payload:

```json
{"pageType":"pc_goods_market"}
```

- This compact JSON is exactly 30 bytes when minified.
- Missing `pageType` or using the wrong shape can return `code: 4010`, message meaning page type parameter error.
- During anti-bot failure this endpoint returned `403 Forbidden` and `set-cookie: acw_tc=...`.
- After recovery, it returns market filter metadata, not product prices.
- Response `data[]` contains filter groups such as `Type`, `Rarity`, `Quality`, `Exterior`, `Color`, `Collection`, and `PrintGunSearch`.
- Useful case/capsule filter examples under `Collection -> weapon case collection` include `property_286` (Kilowatt), `property_287` (Revolution), `property_288` (Recoil), `property_290` (Snakebite), `property_85` (Fracture), and `property_297` (Gallery).

This endpoint is useful for discovery of filter ids and hash names, but it does not contain item price fields.

## Useful Support Endpoints

```http
GET https://api.youpin898.com/api/youpin/pc/query/filter/getSearchTags
GET https://api.youpin898.com/api/youpin/pc/query/filter/getInventorySearchInfo
```

These are filter/search metadata endpoints, not market price list endpoints.

## Sensitive Header Names

Keep names, redact values:

```http
authorization: <redacted>
cookie: <redacted>
uk: <redacted>
deviceUk: <redacted>
deviceId: <redacted>
acw_tc: <redacted>
```

Non-secret request context observed:

```http
appType: 1
platform: pc
AppVersion: 5.26.0
App-Version: 5.26.0
secret-v: h5_v1
origin: https://youpin898.com
referer: https://youpin898.com/
```

When called outside the logged-in browser without auth headers, `querySaleTemplate` can return:

```json
{"Code":85100,"Msg":"current app version is too low; update required"}
```

Treat this as "browser-auth context not reproduced yet" unless the same response occurs in Chrome.

## Confirmed Visible Page Fields

From `https://youpin898.com/market`:

```text
item name: AK-47 | redline-style localized item names, etc.
sale price: yuan price text such as "Y197.5" on page, rendered with RMB symbol in Chrome
sale count: text such as "1000+ in sale"
pagination: current page plus high page count, observed up to 1500
```

Use the API response to replace these DOM-derived labels with exact JSON field names.

## Still Needed

## Price List Endpoint

```http
POST https://api.youpin898.com/api/homepage/pc/goods/market/querySaleTemplate
```

Request payload:

```json
{
  "listSortType": 0,
  "sortType": 0,
  "pageSize": 20,
  "pageIndex": 1
}
```

Observed `content-length` is 59 bytes when the browser sends this compact JSON.

Known response shape:

```json
{
  "Code": 0,
  "Msg": "success",
  "Data": [
    {
      "id": 1672,
      "gameId": 730,
      "commodityName": "localized item name",
      "commodityHashName": "Steam hash name",
      "iconUrl": "small image",
      "iconUrlLarge": "large image",
      "onSaleCount": 317,
      "onLeaseCount": 232,
      "leaseUnitPrice": "0.05",
      "longLeaseUnitPrice": "0.05",
      "leaseDeposit": "344",
      "price": "290",
      "steamPrice": "374.34",
      "steamUsdPrice": "46.07",
      "typeName": "category",
      "exterior": "wear name",
      "rarity": "rarity",
      "quality": "quality",
      "rent": "0.05",
      "listType": 10
    }
  ]
}
```

Confirmed price-list fields:

```text
item id: Data[].id
item name: Data[].commodityName
steam/hash name: Data[].commodityHashName
sale price: Data[].price
steam CNY price: Data[].steamPrice
steam USD price: Data[].steamUsdPrice
sale count/listing count: Data[].onSaleCount
lease count: Data[].onLeaseCount
short rent price: Data[].leaseUnitPrice or Data[].rent
long rent price: Data[].longLeaseUnitPrice
lease deposit: Data[].leaseDeposit
item category: Data[].typeName
wear/exterior: Data[].exterior
quality: Data[].quality
rarity: Data[].rarity
list type: Data[].listType
```

The filter endpoint above is not enough for price crawling; use it only for tag discovery.

## Failure Classification

```text
403 + acw_tc: anti-bot/gateway rejection; stop for 30-60 minutes.
429: rate limit or anti-bot throttling; stop for 30-60 minutes.
84101 or login expired message: auth/login state invalid.
4010: payload/schema parameter problem.
500/502/504: backend outage; do not retry aggressively.
```
