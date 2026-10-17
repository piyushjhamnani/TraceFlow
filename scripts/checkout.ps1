# Usage:  .\scripts\checkout.ps1 -Mode normal|slow|fail [-UserId 1]
param(
    [ValidateSet("normal", "slow", "fail")]
    [string]$Mode = "normal",
    [int]$UserId = 1,
    [string]$BaseUrl = "http://127.0.0.1:8001"
)

$body = @{ item = "laptop"; amount = 499.99; payment_mode = $Mode } | ConvertTo-Json
$timer = [System.Diagnostics.Stopwatch]::StartNew()

try {
    $resp = Invoke-WebRequest -Uri "$BaseUrl/users/$UserId/checkout" -Method Post `
        -ContentType "application/json" -Body $body -UseBasicParsing
    $status = [int]$resp.StatusCode
    $content = $resp.Content
}
catch {
    $errResp = $_.Exception.Response
    if ($null -eq $errResp) {
        Write-Host "Request failed: $($_.Exception.Message)" -ForegroundColor Red
        exit 1
    }
    $status = [int]$errResp.StatusCode
    $content = $_.ErrorDetails.Message
}
$timer.Stop()

$color = if ($status -lt 400) { "Green" } else { "Red" }
Write-Host "Mode: $Mode | HTTP $status | $($timer.ElapsedMilliseconds) ms" -ForegroundColor $color
try   { $content | ConvertFrom-Json | ConvertTo-Json -Depth 10 }
catch { $content }