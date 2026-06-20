$ErrorActionPreference = "Stop"
Push-Location "$PSScriptRoot\..\testdata"
try {
    python universe_scanner.py --reports full --parallel --workers auto --run-robustness --make-chatgpt-report
}
finally {
    Pop-Location
}
