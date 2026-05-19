# Copy the full DevTools Headers text for querySaleTemplate, then run this script.
# It extracts only the values needed by uu_market_probe.py into this PowerShell process.

$raw = Get-Clipboard -Raw
if ([string]::IsNullOrWhiteSpace($raw)) {
    Write-Error "Clipboard is empty. Copy the querySaleTemplate Headers text first."
    exit 1
}

function Get-HeaderValue {
    param(
        [string]$Text,
        [string]$Name
    )

    $escaped = [regex]::Escape($Name)

    # Chrome sometimes copies headers as:
    # authorization
    # token-value
    $twoLinePattern = "(?im)^\s*$escaped\s*\r?\n\s*(.+?)\s*$"
    $match = [regex]::Match($Text, $twoLinePattern)
    if ($match.Success) {
        return $match.Groups[1].Value.Trim()
    }

    # Other copies look like:
    # authorization: token-value
    $colonPattern = "(?im)^\s*$escaped\s*:\s*(.+?)\s*$"
    $match = [regex]::Match($Text, $colonPattern)
    if ($match.Success) {
        return $match.Groups[1].Value.Trim()
    }

    return ""
}

$authorization = Get-HeaderValue -Text $raw -Name "authorization"
$uk = Get-HeaderValue -Text $raw -Name "uk"
$deviceUk = Get-HeaderValue -Text $raw -Name "deviceuk"
$deviceId = Get-HeaderValue -Text $raw -Name "deviceid"

$missing = @()
if (-not $authorization) { $missing += "authorization" }
if (-not $uk) { $missing += "uk" }
if (-not $deviceUk) { $missing += "deviceuk" }
if (-not $deviceId) { $missing += "deviceid" }

if ($missing.Count -gt 0) {
    Write-Error ("Missing required headers: " + ($missing -join ", "))
    Write-Host "Tip: In DevTools Network, select querySaleTemplate, click Headers, then copy the full Request Headers section."
    exit 1
}

$env:UU_AUTHORIZATION = $authorization
$env:UU_UK = $uk
$env:UU_DEVICE_UK = $deviceUk
$env:UU_DEVICE_ID = $deviceId

$env:UU_MODE = if ($env:UU_MODE) { $env:UU_MODE } else { 'sale' }
$env:UU_TEMPLATE_ID = if ($env:UU_TEMPLATE_ID) { $env:UU_TEMPLATE_ID } else { '102276' }
$env:UU_GAME_ID = if ($env:UU_GAME_ID) { $env:UU_GAME_ID } else { '730' }
$env:UU_LIST_TYPE = if ($env:UU_LIST_TYPE) { $env:UU_LIST_TYPE } else { '10' }
$env:UU_PAGE_INDEX = if ($env:UU_PAGE_INDEX) { $env:UU_PAGE_INDEX } else { '1' }
$env:UU_PAGE_SIZE = if ($env:UU_PAGE_SIZE) { $env:UU_PAGE_SIZE } else { '10' }
$env:UU_LIMIT = if ($env:UU_LIMIT) { $env:UU_LIMIT } else { '5' }
$env:UU_OUTPUT = if ($env:UU_OUTPUT) { $env:UU_OUTPUT } else { 'summary' }

python D:/Codex/uu-market-crawler/uu_market_probe.py
