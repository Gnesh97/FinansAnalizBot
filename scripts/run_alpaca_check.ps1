$ErrorActionPreference = "Stop"
Push-Location "$PSScriptRoot\..\testdata"
try {
    python universe_scanner.py --alpaca-paper-check --make-chatgpt-report
}
finally {
    Pop-Location
}
