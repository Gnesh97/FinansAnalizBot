$ErrorActionPreference = "Stop"
Push-Location "$PSScriptRoot\..\testdata"
try {
    python universe_scanner.py --reports basic --parallel --workers auto --paper-report --make-chatgpt-report
}
finally {
    Pop-Location
}
