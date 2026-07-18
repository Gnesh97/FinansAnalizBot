#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR/.."
PYTHON="$PROJECT_ROOT/.venv_linux/bin/python"

SCANNER_PARALLEL_FLAGS="--parallel --workers auto"
DATA_REFRESH_FLAGS="--parallel --download-workers 4 --aggregation-workers auto"

PIPELINE_START_TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

cd "$PROJECT_ROOT/testdata"

echo "Scanner parallel flags: $SCANNER_PARALLEL_FLAGS"
echo "Data refresh flags: $DATA_REFRESH_FLAGS"
echo

echo "[1/4] Updating market data..."
"$PYTHON" vericekici_updated.py --dataset-profile rs_v2_sip --refresh-daily --live $DATA_REFRESH_FLAGS

echo
echo "[2/4] Running paper report and pending fill check..."
"$PYTHON" universe_scanner.py --reports basic $SCANNER_PARALLEL_FLAGS --paper-report --make-chatgpt-report

echo
echo "[3/4] Running execution preview and pre-submit safety check..."
"$PYTHON" universe_scanner.py --execution-preview --pre-submit-check --make-chatgpt-report $SCANNER_PARALLEL_FLAGS

echo
echo "[4/4] Done."
PIPELINE_END_TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
echo "Pipeline start: $PIPELINE_START_TS, end: $PIPELINE_END_TS"
echo "Master report: reports/latest/chatgpt_master_report.md"
