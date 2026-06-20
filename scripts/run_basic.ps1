$ErrorActionPreference = "Stop"
Push-Location "$PSScriptRoot\..\testdata"
try {
    python universe_scanner.py --reports basic --parallel --workers auto
}
finally {
    Pop-Location
}
