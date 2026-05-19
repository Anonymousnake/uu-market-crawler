# Fill these values from Chrome DevTools -> Network -> querySaleTemplate -> Headers.
# Do not commit or share a filled copy of this file.

$env:UU_AUTHORIZATION = '<copy authorization header value here>'
$env:UU_UK = '<copy uk header value here>'
$env:UU_DEVICE_UK = '<copy deviceuk header value here>'
$env:UU_DEVICE_ID = '<copy deviceid header value here>'

# Optional tuning:
$env:UU_MODE = 'sale'
$env:UU_PAGE_INDEX = '1'
$env:UU_PAGE_SIZE = '20'
$env:UU_LIMIT = '5'
$env:UU_OUTPUT = 'summary'

python D:/Codex/uu-market-crawler/uu_market_probe.py
