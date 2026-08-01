import os
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone
import requests

# Dizin tanımları
BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
CONFIG_PATH = BASE_DIR / "config.json"

# Python path'ine testdata dizinini ekle
sys.path.append(str(BASE_DIR / "testdata"))

def send_telegram_message(message, chat_id=None, parse_mode=None, reply_markup=None):
    target_chat_id = chat_id if chat_id else TELEGRAM_CHAT_ID
    if not target_chat_id:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": target_chat_id, "text": message}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except Exception as err:
        print(f"Telegram gönderim hatası: {err}")
        return False

# Centralized Safety Configuration Flags
BROKER_SUBMIT_ENABLED = False
LIVE_TRADING_ENABLED = False
SHORT_TRADING_ENABLED = False
ALPACA_PAPER_ONLY = True
MAX_NEW_ORDERS_PER_RUN = 5
MAX_ORDER_PCT_DOLLAR_VOLUME = 0.01

# .env Dosyasını elle okuyan yardımcı fonksiyon
def load_dotenv(env_path):
    if not env_path.exists():
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip()

# Env ve config yükle
load_dotenv(ENV_PATH)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Chat ID integer olmalıdır
if TELEGRAM_CHAT_ID:
    try:
        TELEGRAM_CHAT_ID = int(TELEGRAM_CHAT_ID)
    except ValueError:
        print("TELEGRAM_CHAT_ID geçerli bir sayı değil!")

# Config dosyasını yükle ve safety flag denetimi yap
def load_config():
    global BROKER_SUBMIT_ENABLED, LIVE_TRADING_ENABLED, SHORT_TRADING_ENABLED

    config = {}
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception as e:
            print(f"Warning: could not load config.json: {e}")

    # Canlı işlem/gönderim bayraklarının izinsiz açılma denetimi
    attempts = []
    if config.get("broker_submit_enabled") is True:
        attempts.append("config.json: broker_submit_enabled=True")
    if config.get("live_trading_enabled") is True:
        attempts.append("config.json: live_trading_enabled=True")
    if os.environ.get("BROKER_SUBMIT_ENABLED") == "True" or os.environ.get("BROKER_SUBMIT_ENABLED") == "1":
        attempts.append("env: BROKER_SUBMIT_ENABLED=True")
    if os.environ.get("LIVE_TRADING_ENABLED") == "True" or os.environ.get("LIVE_TRADING_ENABLED") == "1":
        attempts.append("env: LIVE_TRADING_ENABLED=True")

    if attempts:
        print("\n" + "!" * 50)
        print("⚠️  WARNING: Unauthorized attempt to enable live/submit trading detected!")
        for attempt in attempts:
            print(f"  - Attempt: {attempt}")
        print("Safety locks are ACTIVE. Live submit is HARD-BLOCKED in this build.")
        print("Forcing safety flags to FALSE.")
        print("!" * 50 + "\n")

    # Her koşulda zorla False yap
    BROKER_SUBMIT_ENABLED = False
    LIVE_TRADING_ENABLED = False
    SHORT_TRADING_ENABLED = False
    return config

# Command line parsing
def parse_args():
    parser = argparse.ArgumentParser(
        description="US Equities Systematic Cockpit & Post-Backtest Execution Controller."
    )
    parser.add_argument(
        "--mode",
        choices=["dss-report"],
        default="dss-report",
        help="Run mode selection. Only dss-report is implemented; see git history for the "
             "removed status/local-paper/execution-preview/pre-submit-check/alpaca-check/"
             "backup/interactive modes."
    )
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="dss-report mode only: render the report to stdout/file, never send to Telegram."
    )
    parser.add_argument(
        "--dss-data-profile",
        choices=["rs_v2_sip", "rs_v2_clean", "rs_v2_tiingo"],
        default="rs_v2_sip",
        help="dss-report mode only: which validated dataset profile to read. "
             "Default rs_v2_sip; rs_v2_clean selects the IEX-feed dataset; "
             "rs_v2_tiingo selects the Tiingo daily-only dataset."
    )
    return parser.parse_args()

# =====================================================================
# DSS (DECISION SUPPORT) DAILY BUY/SELL REPORT -- READ-ONLY, MANUAL TRIGGER
# =====================================================================
# No broker/order/live-trading call exists anywhere in this section.
assert BROKER_SUBMIT_ENABLED is False
assert LIVE_TRADING_ENABLED is False
assert SHORT_TRADING_ENABLED is False


def send_dss_report_message(text, chat_id):
    """Small, mockable wrapper around send_telegram_message. Never called
    with broker/order parameters -- text and chat_id only."""
    return send_telegram_message(text, chat_id)


def run_dss_report(args):
    print("==================================================")
    print("📋 [MODE] Mode: dss-report")
    print("==================================================")

    from testdata.dss_report import generate_dss_report, render_dss_report_text

    dss_data_profile = getattr(args, "dss_data_profile", "rs_v2_sip")
    data_folder = BASE_DIR / "testdata" / "research_data" / f"{dss_data_profile}_market_data"
    reports_dir = BASE_DIR / "reports" / "dss"
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifact_path = reports_dir / f"dss_report_{timestamp}.md"

    try:
        report = generate_dss_report(data_folder)
        text = render_dss_report_text(report)
    except Exception as e:
        text = f"DSS did not run today: {e}"
        artifact_path.write_text(text, encoding="utf-8")
        print(text)
        print(f"\nSaved: {artifact_path}")
        if args.print_only:
            print("\n--print-only: not sending to Telegram.")
            return
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            print("ERROR: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set in .env -- "
                  "failure notice NOT sent (fail-closed locally, not sent to a wrong/missing chat).")
            return
        send_dss_report_message(text, TELEGRAM_CHAT_ID)
        return

    artifact_path.write_text(text, encoding="utf-8")
    print(text)
    print(f"\nSaved: {artifact_path}")

    if args.print_only:
        print("\n--print-only: not sending to Telegram.")
        return

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("ERROR: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set in .env -- "
              "refusing to send (fail-closed locally, not sent to a wrong/missing chat).")
        return

    sent = send_dss_report_message(text, TELEGRAM_CHAT_ID)
    if sent:
        print("✅ DSS report sent to Telegram.")
    else:
        print("❌ Failed to send DSS report to Telegram (see error above).")


# Ana Giriş Noktası
def main():
    args = parse_args()
    load_config()

    if args.mode == "dss-report":
        run_dss_report(args)

if __name__ == "__main__":
    main()
