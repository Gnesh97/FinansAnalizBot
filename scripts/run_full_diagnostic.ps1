$ErrorActionPreference = "Stop"
Push-Location "$PSScriptRoot\..\testdata"
try {
    python universe_scanner.py --reports basic --parallel --workers auto --run-oos --run-attribution --run-cost-stress --run-liquidity-guard-sensitivity --make-chatgpt-report
}
finally {
    Pop-Location
}
