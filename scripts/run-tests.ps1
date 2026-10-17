# Runs every test suite. Each component is its own Python project,
# so each suite runs separately.
$suites = "services/user-service", "services/order-service", "services/payment-service", "traceflow"
$failed = @()

foreach ($suite in $suites) {
    Write-Host "`n=== Testing $suite ===" -ForegroundColor Cyan
    python -m pytest $suite -q
    if ($LASTEXITCODE -ne 0) { $failed += $suite }
}

if ($failed.Count -gt 0) {
    Write-Host "`nFAILED: $($failed -join ', ')" -ForegroundColor Red
    exit 1
}
Write-Host "`nAll test suites passed." -ForegroundColor Green