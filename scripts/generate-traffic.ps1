# Sends a shuffled mix of checkout requests to produce realistic traffic.
# Usage:  .\scripts\generate-traffic.ps1 [-Normal 20] [-Slow 2] [-Fail 2]
param(
    [int]$Normal = 20,
    [int]$Slow = 2,
    [int]$Fail = 2,
    [string]$BaseUrl = "http://127.0.0.1:8001"
)

$modes = @()
$modes += @("normal") * $Normal
$modes += @("slow") * $Slow
$modes += @("fail") * $Fail
$modes = @($modes | Get-Random -Count $modes.Count)   # shuffle

Write-Host "Sending $($modes.Count) requests ($Normal normal, $Slow slow, $Fail fail)..."
$i = 0
foreach ($mode in $modes) {
    $i++
    $userId = Get-Random -Minimum 1 -Maximum 4
    $body = @{ item = "laptop"; amount = 499.99; payment_mode = $mode } | ConvertTo-Json
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $resp = Invoke-WebRequest -Uri "$BaseUrl/users/$userId/checkout" -Method Post `
            -ContentType "application/json" -Body $body -UseBasicParsing
        $status = [int]$resp.StatusCode
    }
    catch {
        if ($null -eq $_.Exception.Response) {
            Write-Host "[$i/$($modes.Count)] $mode -> request failed: $($_.Exception.Message)" -ForegroundColor Red
            continue
        }
        $status = [int]$_.Exception.Response.StatusCode
    }
    $timer.Stop()
    $color = if ($status -lt 400) { "Green" } else { "Red" }
    Write-Host ("[{0}/{1}] {2,-6} user={3} HTTP {4} {5} ms" -f $i, $modes.Count, $mode, $userId, $status, $timer.ElapsedMilliseconds) -ForegroundColor $color
}
Write-Host "Done. Wait about 15 seconds, then check: Invoke-RestMethod http://127.0.0.1:8000/health"