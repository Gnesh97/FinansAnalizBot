import os
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
import json
import time
import argparse
import shutil
import schedule
import threading
import subprocess
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
import requests
import matplotlib
matplotlib.use("Agg")  # GUI pencerelerinin açılmasını engeller
import matplotlib.pyplot as plt
import yfinance as yf

# Dizin tanımları
BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
CONFIG_PATH = BASE_DIR / "config.json"

# Python path'ine testdata dizinini ekle
sys.path.append(str(BASE_DIR / "testdata"))

PIPELINE_LOCK = threading.Lock()

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

def send_telegram_document(document_path, caption=None, chat_id=None):
    target_chat_id = chat_id if chat_id else TELEGRAM_CHAT_ID
    if not target_chat_id:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    data = {"chat_id": target_chat_id}
    if caption:
        data["caption"] = caption
    try:
        with open(document_path, "rb") as f:
            files = {"document": f}
            response = requests.post(url, data=data, files=files, timeout=30)
            response.raise_for_status()
        return True
    except Exception as err:
        print(f"Telegram döküman gönderim hatası: {err}")
        return False

def answer_callback_query(callback_query_id, text=None, show_alert=False):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
    payload = {
        "callback_query_id": callback_query_id,
        "text": text,
        "show_alert": show_alert
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except Exception as err:
        print(f"Telegram answerCallbackQuery hatası: {err}")
        return False

def edit_message_text(chat_id, message_id, text, parse_mode=None, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except Exception as err:
        print(f"Telegram editMessageText hatası: {err}")
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
        choices=["status", "local-paper", "execution-preview", "pre-submit-check", "alpaca-check", "backup", "interactive", "dss-report"],
        default="status",
        help="Run mode selection. Default is status."
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable detailed logging."
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

# Mode status
def run_status(args):
    print("==================================================")
    print("🔍 [MODE] Mode: status")
    print("==================================================")
    
    print("Safety Configurations:")
    print(f"  - broker_submit_enabled: {BROKER_SUBMIT_ENABLED}")
    print(f"  - live_trading_enabled: {LIVE_TRADING_ENABLED}")
    print(f"  - short_trading_enabled: {SHORT_TRADING_ENABLED}")
    print(f"  - alpaca_paper_only: {ALPACA_PAPER_ONLY}")
    print(f"  - max_new_orders_per_run: {MAX_NEW_ORDERS_PER_RUN}")
    print(f"  - max_order_pct_dollar_volume: {MAX_ORDER_PCT_DOLLAR_VOLUME}")
    print(f"  - short_readiness_status: BLOCK")
    print(f"  - short_submit_allowed: False")
    print(f"  - short_strategy_enabled: False")
    print(f"  - submit_allowed: False")
    print("")

    latest_dir = BASE_DIR / "testdata" / "reports" / "latest"
    
    # 1. Review Orders
    review_file = latest_dir / "paper_order_review.csv"
    accepted_count = 0
    rejected_count = 0
    hold_review_count = 0
    if review_file.exists():
        try:
            df = pd.read_csv(review_file)
            df.columns = df.columns.str.strip()
            decisions = df["user_decision"].str.strip().str.upper()
            accepted_count = int(decisions.eq("ACCEPT").sum())
            rejected_count = int(decisions.eq("REJECT").sum())
            hold_review_count = int(decisions.eq("HOLD_REVIEW").sum())
        except Exception as e:
            if args.verbose:
                print(f"Warning: could not read review file: {e}")
            
    # 2. Pending Orders
    pending_file = latest_dir / "paper_pending_orders.csv"
    pending_order_count = 0
    pending_next_open_count = 0
    if pending_file.exists():
        try:
            df = pd.read_csv(pending_file)
            df.columns = df.columns.str.strip()
            pending_order_count = len(df)
            if "status" in df.columns:
                pending_next_open_count = int(df["status"].str.strip().str.upper().eq("PENDING_NEXT_OPEN").sum())
            elif "fill_status" in df.columns:
                pending_next_open_count = int(df["fill_status"].str.strip().str.upper().eq("PENDING_NEXT_OPEN").sum())
        except Exception as e:
            if args.verbose:
                print(f"Warning: could not read pending file: {e}")
            
    # 3. Trade Log Rows
    trade_log_file = latest_dir / "paper_trade_log.csv"
    trade_log_rows = 0
    if trade_log_file.exists():
        try:
            df = pd.read_csv(trade_log_file)
            trade_log_rows = len(df)
        except Exception as e:
            if args.verbose:
                print(f"Warning: could not read trade log: {e}")

    # 4. Pipeline Health Log
    health_file = latest_dir / "pipeline_health_log.csv"
    latest_run_date = "N/A"
    latest_daily_date = "N/A"
    latest_130min_date = "N/A"
    stale_data_warning = "False"
    filled_count_this_run = 0
    if health_file.exists():
        try:
            df = pd.read_csv(health_file)
            df.columns = df.columns.str.strip()
            if not df.empty:
                last_row = df.iloc[-1]
                latest_run_date = last_row.get("run_datetime", "N/A")
                latest_daily_date = last_row.get("latest_daily_date", "N/A")
                latest_130min_date = last_row.get("latest_130min_date", "N/A")
                stale_data_warning = str(last_row.get("stale_data_warning", "False"))
                filled_count_this_run = int(last_row.get("filled_count_this_run", 0))
        except Exception as e:
            if args.verbose:
                print(f"Warning: could not read health log: {e}")

    print("State Counts & Data Freshness:")
    print(f"  - pending_order_count: {pending_order_count}")
    print(f"  - accepted_order_count: {accepted_count}")
    print(f"  - rejected_order_count: {rejected_count}")
    print(f"  - hold_review_count: {hold_review_count}")
    print(f"  - pending_next_open_count: {pending_next_open_count}")
    print(f"  - filled_count_this_run: {filled_count_this_run}")
    print(f"  - trade_log_rows: {trade_log_rows}")
    print(f"  - latest_signal_date: {latest_run_date}")
    print(f"  - latest_data_date: Daily={latest_daily_date}, 130min={latest_130min_date}")
    print(f"  - stale_data_warning: {stale_data_warning}")
    print("")
    
    print("Execution Readiness Summary:")
    print("  - broker_submit_enabled: False (Intentionally blocked)")
    print("  - live_trading_enabled: False (Intentionally blocked)")
    print("  - submit_order_called: False")
    print("  - post_request_used: False")
    print("  - would_submit: False")
    print("  - submit_allowed: False")
    print("")
    print("👉 Next Recommended Action: Run 'python bot.py --mode local-paper' to process signals or 'python bot.py --mode execution-preview' to generate previews.")
    print("==================================================")

# Mode local-paper
def run_local_paper(args):
    print("==================================================")
    print("🔄 [MODE] Mode: local-paper")
    print("Running local paper trading experiments...")
    print("==================================================")
    
    from testdata.runner import run_strategy_experiments
    from testdata.universe_scanner import VALIDATION_EXPERIMENTS
    
    data_folder_path = BASE_DIR / "testdata" / "hisseler"
    
    run_strategy_experiments(
        data_folder=str(data_folder_path),
        experiments=VALIDATION_EXPERIMENTS,
        run_profile="fast",
        quick_mode=True,
        compact_report=True,
        write_experiment_detail_files=False,
        reports="basic",
        parallel=True,
        workers="auto",
        make_chatgpt_report=True,
        paper_report=True,
        paper_strategy="main",
        paper_include_watchlist=False,
        command="python bot.py --mode local-paper"
    )
    
    print("\n✅ Local paper trading simulation complete.")
    run_status(args)

# Mode execution-preview
def run_execution_preview(args):
    print("==================================================")
    print("🔍 [MODE] Mode: execution-preview")
    print("Generating dry-run execution preview...")
    print("==================================================")
    
    from testdata.runner import run_execution_preview_check
    
    data_folder_path = BASE_DIR / "testdata" / "hisseler"
    
    summary = run_execution_preview_check(
        data_folder=str(data_folder_path),
        make_chatgpt_report=True,
        pre_submit_check=False
    )
    
    print("\n✅ Execution preview generation complete.")
    print("Safety Audit:")
    print("  - broker_submit_enabled: False")
    print("  - live_trading_enabled: False")
    print("  - submit_order_called: False")
    print("  - post_request_used: False")
    print("  - would_submit: False")
    print("  - submit_allowed: False")
    print("")
    print("👉 Next Recommended Action: Run 'python bot.py --mode pre-submit-check' to verify safety checklists.")

# Mode pre-submit-check
def run_pre_submit_check(args):
    print("==================================================")
    print("🛡️ [MODE] Mode: pre-submit-check")
    print("Running pre-submit safety gates...")
    print("==================================================")
    
    from testdata.runner import run_execution_preview_check
    
    data_folder_path = BASE_DIR / "testdata" / "hisseler"
    
    summary = run_execution_preview_check(
        data_folder=str(data_folder_path),
        make_chatgpt_report=True,
        pre_submit_check=True
    )
    
    print("\n✅ Pre-submit safety check complete.")
    print("Readiness Status:")
    print("  - pre_submit_readiness_status: BLOCK")
    print("  - submit_allowed: False")
    print("  - short_readiness_status: BLOCK")
    print("  - short_submit_allowed: False")
    print("")
    print("👉 Next Recommended Action: Analyze 'reports/latest/pre_submit_readiness_report.md'.")

# Mode alpaca-check
def run_alpaca_check(args):
    print("==================================================")
    print("🦙 [MODE] Mode: alpaca-check")
    print("Running GET-only Alpaca connection check...")
    print("==================================================")
    
    from testdata.runner import run_alpaca_paper_check
    
    data_folder_path = BASE_DIR / "testdata" / "hisseler"
    
    run_alpaca_paper_check(
        data_folder=str(data_folder_path),
        make_chatgpt_report=True
    )
    print("\n✅ Alpaca paper endpoint check complete.")

# Mode backup
def run_backup(args):
    print("==================================================")
    print("💾 [MODE] Mode: backup")
    print("Backing up latest reports...")
    print("==================================================")
    
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_dir = BASE_DIR / "testdata" / "backups" / stamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    
    latest_dir = BASE_DIR / "testdata" / "reports" / "latest"
    files_to_backup = [
        "chatgpt_master_report.md",
        "paper_signal_report.md",
        "paper_signal_report.csv",
        "paper_pending_orders.csv",
        "paper_order_review.csv",
        "paper_trade_log.csv",
        "paper_trading_plan.md",
        "order_intents.csv",
        "order_risk_check_report.csv",
        "order_risk_check_report.md",
        "execution_preview_report.csv",
        "execution_preview_report.md",
        "execution_audit_log.csv",
        "pre_submit_readiness_report.csv",
        "pre_submit_readiness_report.md",
        "short_readiness_report.csv",
        "short_readiness_report.md",
        "daily_preflight_report.md",
        "data_precheck_report.md",
        "regression_guard_report.md",
        "pipeline_health_log.csv",
        "run_manifest.json",
        "test_run_report.md",
        "paper_state_validation_report.md",
        "report_schema_validation_report.md",
        "project_status_detailed_report.md",
        "performance_profile_report.md",
        "strategy_compact_report.md",
        "strategy_compact_report.csv",
        "benchmark_audit_report.md",
        "data_audit_report.md",
        "data_audit_report.csv"
    ]
    
    copied = 0
    for filename in files_to_backup:
        src = latest_dir / filename
        if src.exists():
            shutil.copy2(src, backup_dir / filename)
            copied += 1
            if args.verbose:
                print(f"Copied: {filename}")
                
    print(f"\n✅ Backup complete: {copied} files saved to {backup_dir.resolve()}")


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


# =====================================================================
# TELEGRAM COCKPIT INTERACTIVE MODE (Önceki Turn'de Kodlanan Kısım)
# =====================================================================

# Inline Keyboard Buton Yapıcı
def build_inline_keyboard(symbol):
    return json.dumps({
        "inline_keyboard": [
            [
                {"text": "✅ Kabul", "callback_data": f"accept_{symbol}"},
                {"text": "❌ Ret", "callback_data": f"reject_{symbol}"},
                {"text": "⏳ İncele", "callback_data": f"hold_{symbol}"}
            ]
        ]
    })

# Fiyat Grafiği Oluşturma Mantığı
def create_price_chart(symbol, tail=100):
    symbol = symbol.strip().upper()
    csv_path = BASE_DIR / "testdata" / "hisseler" / "daily" / f"{symbol}_1D.csv"
    if not csv_path.exists():
        return None
        
    try:
        df_full = pd.read_csv(csv_path)
        df_full.columns = df_full.columns.str.strip()
        df_full["Date"] = pd.to_datetime(df_full["Date"])
        df_full = df_full.sort_values("Date")
        df_full["SMA_50"] = df_full["Close"].rolling(min(50, len(df_full))).mean()
        df_full["SMA_200"] = df_full["Close"].rolling(min(200, len(df_full))).mean()
        
        df_plot = df_full.tail(tail)
        
        plt.figure(figsize=(10, 5))
        plt.plot(df_plot["Date"], df_plot["Close"], label="Fiyat", color="blue", linewidth=1.5)
        if not df_plot["SMA_50"].isna().all():
            plt.plot(df_plot["Date"], df_plot["SMA_50"], label="SMA 50", color="orange", linestyle="--", linewidth=1.2)
        if not df_plot["SMA_200"].isna().all():
            plt.plot(df_plot["Date"], df_plot["SMA_200"], label="SMA 200", color="green", linestyle="--", linewidth=1.2)
            
        plt.title(f"{symbol} - Fiyat ve Teknik Göstergeler (Son {tail} Gün)")
        plt.xlabel("Tarih")
        plt.ylabel("Fiyat ($)")
        plt.legend(loc="upper left")
        plt.grid(True, linestyle=":", alpha=0.6)
        
        temp_path = BASE_DIR / f"temp_chart_{symbol}.png"
        plt.savefig(temp_path, dpi=150, bbox_inches="tight")
        plt.close()
        return temp_path
    except Exception as e:
        print(f"Grafik çizilirken hata oluştu ({symbol}): {e}")
        return None

# Karar Güncelleme Mantığı (paper_order_review.csv)
def update_user_decision(symbol, decision, notes=""):
    symbol = symbol.strip().upper()
    decision = decision.strip().upper()  # ACCEPT, REJECT, HOLD_REVIEW
    csv_path = BASE_DIR / "testdata" / "reports" / "latest" / "paper_order_review.csv"
    if not csv_path.exists():
        return False, "⚠️ Onay listesi bulunamadı. Lütfen önce /scan komutunu çalıştırın."
    
    try:
        df = pd.read_csv(csv_path)
        df.columns = df.columns.str.strip()
        if df.empty:
            return False, "ℹ️ Onay listesinde bekleyen işlem bulunmuyor."
            
        if symbol == "ALL":
            df["user_decision"] = decision
            df.to_csv(csv_path, index=False)
            return True, f"✅ Tüm işlemlerin kararı '{decision}' olarak güncellendi."
            
        symbol_mask = df["symbol"].str.strip().str.upper() == symbol
        if not symbol_mask.any():
            return False, f"⚠️ Onay listesinde <b>{symbol}</b> için bekleyen işlem bulunamadı."
            
        df.loc[symbol_mask, "user_decision"] = decision
        if notes:
            df.loc[symbol_mask, "notes"] = notes
            
        df.to_csv(csv_path, index=False)
        return True, f"✅ <b>{symbol}</b> kararı '{decision}' olarak güncellendi."
    except Exception as e:
        return False, f"❌ Dosya güncellenirken hata oluştu: {str(e)}"

# Aktif Pozisyonları Trade Logundan Hesaplama
def get_active_positions():
    csv_path = BASE_DIR / "testdata" / "reports" / "latest" / "paper_trade_log.csv"
    if not csv_path.exists():
        return []
    try:
        df = pd.read_csv(csv_path)
        df.columns = df.columns.str.strip()
        if df.empty:
            return []
            
        positions = {}
        for _, row in df.iterrows():
            sym = str(row["symbol"]).strip().upper()
            action = str(row["action"]).strip().upper()
            shares = float(row["shares"]) if not pd.isna(row["shares"]) else 0
            price = float(row["assumed_fill_price"]) if not pd.isna(row["assumed_fill_price"]) else 0
            
            if sym not in positions:
                positions[sym] = {"shares": 0.0, "total_cost": 0.0}
                
            if "BUY" in action:
                positions[sym]["shares"] += shares
                positions[sym]["total_cost"] += shares * price
            elif "SELL" in action:
                positions[sym]["shares"] -= shares
                positions[sym]["total_cost"] -= shares * price
                
        active = []
        for sym, data in positions.items():
            if data["shares"] > 0.01:
                avg_price = data["total_cost"] / data["shares"] if data["shares"] > 0 else 0
                active.append({
                    "symbol": sym,
                    "shares": round(data["shares"], 2),
                    "avg_price": round(avg_price, 2)
                })
        return active
    except Exception as e:
        print(f"Pozisyonlar hesaplanırken hata: {e}")
        return []

# Rapor Gönderme Yardımcıları
def send_review_list(chat_id):
    review_path = BASE_DIR / "testdata" / "reports" / "latest" / "paper_order_review.csv"
    if not review_path.exists():
        send_telegram_message("ℹ️ Onay listesi bulunamadı. Lütfen önce /scan komutunu çalıştırın.", chat_id)
        return
        
    try:
        df = pd.read_csv(review_path)
        df.columns = df.columns.str.strip()
        
        pending = df[df["user_decision"].str.strip().str.upper() == "HOLD_REVIEW"]
        if pending.empty:
            send_telegram_message("ℹ️ Onay bekleyen (HOLD_REVIEW) sinyal bulunmuyor.", chat_id)
            return
            
        send_telegram_message(f"📋 Onay bekleyen {len(pending)} sinyal için grafikler ve butonlar hazırlanıyor...", chat_id)
        
        for idx, row in pending.iterrows():
            sym = row["symbol"]
            act = row["action"]
            strat = row["strategy_name"]
            dec = row["user_decision"]
            
            caption = (
                f"🚨 <b>Sinyal Detayı: {sym}</b>\n"
                f"• Aksiyon: <b>{act}</b>\n"
                f"• Strateji: <code>{strat}</code>\n"
                f"• Mevcut Karar: <b>{dec}</b>\n\n"
                f"⚠️ <i>Lütfen aşağıdaki butonları kullanarak kararınızı verin:</i>"
            )
            
            reply_markup = build_inline_keyboard(sym)
            chart_path = create_price_chart(sym)
            
            if chart_path and chart_path.exists():
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
                try:
                    with open(chart_path, "rb") as f:
                        files = {"photo": f}
                        data = {
                            "chat_id": chat_id,
                            "caption": caption,
                            "parse_mode": "HTML",
                            "reply_markup": reply_markup
                        }
                        requests.post(url, data=data, files=files, timeout=30)
                except Exception as err:
                    print(f"Fotoğraf gönderim hatası: {err}")
                    send_telegram_message(caption, chat_id, parse_mode="HTML", reply_markup=reply_markup)
                finally:
                    if chart_path.exists():
                        os.remove(chart_path)
            else:
                send_telegram_message(caption, chat_id, parse_mode="HTML", reply_markup=reply_markup)
                
    except Exception as e:
        send_telegram_message(f"❌ Onay listesi okunurken hata: {e}", chat_id)

def send_positions_report(chat_id):
    active = get_active_positions()
    if not active:
        send_telegram_message("💼 Aktif açık pozisyon bulunmuyor.", chat_id)
        return
        
    send_telegram_message("⏳ Pozisyonlar için canlı piyasa fiyatları çekiliyor...", chat_id)
    
    msg = "💼 <b>Aktif Paper Trade Pozisyonları (Canlı PnL):</b>\n\n"
    total_cost = 0.0
    total_value = 0.0
    
    for pos in active:
        sym = pos["symbol"]
        shares = pos["shares"]
        avg_price = pos["avg_price"]
        
        live_price = None
        try:
            stock = yf.Ticker(sym)
            hist = stock.history(period="1d")
            if not hist.empty:
                live_price = float(hist["Close"].iloc[-1])
        except Exception:
            pass
            
        pos_cost = shares * avg_price
        total_cost += pos_cost
        
        if live_price is not None:
            pos_value = shares * live_price
            total_value += pos_value
            pnl_dollars = pos_value - pos_cost
            pnl_pct = (pnl_dollars / pos_cost) * 100 if pos_cost > 0 else 0
            
            pnl_icon = "🟢" if pnl_dollars >= 0 else "🔴"
            msg += (
                f"• <b>{sym}</b>: {shares} Lot\n"
                f"  Maliyet: ${avg_price:.2f} | Güncel: ${live_price:.2f}\n"
                f"  PnL: {pnl_icon} <b>${pnl_dollars:+.2f}</b> (<code>{pnl_pct:+.2f}%</code>)\n\n"
            )
        else:
            total_value += pos_cost
            msg += (
                f"• <b>{sym}</b>: {shares} Lot\n"
                f"  Maliyet: ${avg_price:.2f} | Güncel: <i>Bilinmiyor</i>\n\n"
            )
            
    total_pnl = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost) * 100 if total_cost > 0 else 0
    summary_icon = "🏆" if total_pnl >= 0 else "🚨"
    
    msg += (
        f"<b>Toplam Portföy Durumu:</b>\n"
        f"💰 Yatırım Tutarı: ${total_cost:.2f}\n"
        f"📈 Güncel Değer: ${total_value:.2f}\n"
        f"{summary_icon} Toplam PnL: <b>${total_pnl:+.2f}</b> (<code>{total_pnl_pct:+.2f}%</code>)\n\n"
        f"⚠️ <i>Not: Fiyatlar yfinance üzerinden canlı çekilmiştir.</i>"
    )
    
    send_telegram_message(msg, chat_id, parse_mode="HTML")

def send_status_report(chat_id):
    health_path = BASE_DIR / "testdata" / "reports" / "latest" / "pipeline_health_log.csv"
    health_msg = "ℹ️ Sistem sağlık günlüğü dosyası henüz oluşturulmamış."
    
    if health_path.exists():
        try:
            df = pd.read_csv(health_path)
            df.columns = df.columns.str.strip()
            if not df.empty:
                last_row = df.iloc[-1]
                health_msg = (
                    "📊 <b>Sistem Sağlık Özeti:</b>\n"
                    f"• Son Çalışma: <code>{last_row['run_datetime']}</code>\n"
                    f"• Durum: <b>{last_row['status']}</b>\n"
                    f"• Günlük Veri Tarihi: <code>{last_row['latest_daily_date']}</code>\n"
                    f"• 130dk Veri Tarihi: <code>{last_row['latest_130min_date']}</code>\n"
                    f"• Bekleyen/Kabul Edilen: <code>{last_row['pending_order_count']} / {last_row['accepted_order_count']}</code>\n"
                )
                if str(last_row["warning_summary"]).strip() not in {"", "nan", "None"}:
                    health_msg += f"• Uyarılar: <i>{last_row['warning_summary']}</i>\n"
        except Exception as e:
            health_msg = f"❌ Sağlık günlüğü okunurken hata: {e}"
            
    review_path = BASE_DIR / "testdata" / "reports" / "latest" / "paper_order_review.csv"
    review_summary = ""
    if review_path.exists():
        try:
            df = pd.read_csv(review_path)
            df.columns = df.columns.str.strip()
            pending_count = len(df[df["user_decision"].str.strip().str.upper() == "HOLD_REVIEW"])
            accepted_count = len(df[df["user_decision"].str.strip().str.upper() == "ACCEPT"])
            review_summary = f"\n📋 <b>Onay Durumu:</b> {pending_count} bekleyen, {accepted_count} onaylı sinyal."
        except Exception:
            pass
            
    send_telegram_message(f"{health_msg}{review_summary}", chat_id, parse_mode="HTML")

def send_master_report_file(chat_id):
    report_path = BASE_DIR / "testdata" / "reports" / "latest" / "chatgpt_master_report.md"
    if not report_path.exists():
        send_telegram_message("ℹ️ ChatGPT master raporu henüz oluşturulmamış. Lütfen önce tarama gerçekleştirin.", chat_id)
        return
        
    send_telegram_message("📄 Master Rapor dosyası gönderiliyor...", chat_id)
    success = send_telegram_document(report_path, caption="ChatGPT Master Raporu", chat_id=chat_id)
    if not success:
        send_telegram_message("❌ Rapor dosyası gönderilemedi.", chat_id)

def send_help_message(chat_id):
    help_text = (
        "🤖 <b>Kuantitatif Swing Trading Bot Cockpit</b> (Test Aşaması)\n\n"
        "Kullanılabilir Komutlar:\n"
        "🔄 <b>/scan</b> - Günlük veri güncelleme ve sinyal tarama işlemini başlatır.\n"
        "📋 <b>/review</b> - Onay bekleyen işlem sinyallerini listeler.\n"
        "✅ <b>/accept &lt;SYMBOL&gt;</b> - Hissenin sinyalini onaylar (örn: <code>/accept AMAT</code>). Hepsini onaylamak için: <code>/accept ALL</code>\n"
        "❌ <b>/reject &lt;SYMBOL&gt;</b> - Hissenin sinyalini reddeder (örn: <code>/reject MU</code>). Hepsini reddetmek için: <code>/reject ALL</code>\n"
        "⏳ <b>/hold &lt;SYMBOL&gt;</b> - Hissenin sinyalini incelemeye alır.\n"
        "📊 <b>/status</b> - Son tarama durumunu ve veri güncelliğini sorgular.\n"
        "💼 <b>/positions</b> - Mevcut açık paper trade pozisyonlarını görüntüler.\n"
        "📄 <b>/report</b> - En son üretilen ChatGPT master raporunu gönderir.\n"
        "ℹ️ <b>/help</b> - Bu yardım mesajını görüntüler.\n\n"
        "⚠️ <i>Tüm stratejiler ve paper trading işlemleri deneme/test aşamasındadır.</i>"
    )
    send_telegram_message(help_text, chat_id, parse_mode="HTML")

# Pipeline İşçi Fonksiyonu (Thread) - interaktif modda tetiklenir
def run_pipeline_worker_thread(chat_id):
    global PIPELINE_RUNNING
    with PIPELINE_LOCK:
        if PIPELINE_RUNNING:
            send_telegram_message("⚠️ Zaten çalışan bir tarama işlemi bulunuyor. Lütfen bitmesini bekleyin.", chat_id)
            return
        PIPELINE_RUNNING = True
        
    try:
        send_telegram_message("🔄 Tarama başlatıldı (Test Aşaması Stratejileri)...\n\n[1/3] Güncel piyasa verileri çekiliyor...", chat_id)
        
        # Command 1: Data Update
        cmd1 = [
            sys.executable, "vericekici_updated.py",
            "--universe", "all",
            "--feed", "iex",
            "--adjustment", "all",
            "--daily-start", "2015-01-01",
            "--intraday-start", "2020-07-01",
            "--end", "today",
            "--output-dir", "hisseler"
        ]
        res1 = subprocess.run(cmd1, cwd=BASE_DIR / "testdata", capture_output=True, text=True)
        if res1.returncode != 0:
            send_telegram_message(f"❌ Veri güncelleme başarısız oldu:\n<pre>{res1.stderr[:300]}</pre>", chat_id, parse_mode="HTML")
            return
            
        send_telegram_message("[2/3] Strateji sinyalleri hesaplanıyor (Paper Report)...", chat_id)
        
        # Command 2: Paper Scan
        cmd2 = [
            sys.executable, "universe_scanner.py",
            "--reports", "basic",
            "--parallel",
            "--workers", "auto",
            "--paper-report",
            "--make-chatgpt-report"
        ]
        res2 = subprocess.run(cmd2, cwd=BASE_DIR / "testdata", capture_output=True, text=True)
        if res2.returncode != 0:
            send_telegram_message(f"❌ Sinyal hesaplama başarısız oldu:\n<pre>{res2.stderr[:300]}</pre>", chat_id, parse_mode="HTML")
            return
            
        send_telegram_message("[3/3] Limit ve risk kontrolleri (Güvenlik kapısı) yapılıyor...", chat_id)
        
        # Command 3: Execution Preview
        cmd3 = [
            sys.executable, "universe_scanner.py",
            "--execution-preview",
            "--pre-submit-check",
            "--make-chatgpt-report"
        ]
        res3 = subprocess.run(cmd3, cwd=BASE_DIR / "testdata", capture_output=True, text=True)
        if res3.returncode != 0:
            send_telegram_message(f"❌ Güvenlik kapısı kontrolü başarısız oldu:\n<pre>{res3.stderr[:300]}</pre>", chat_id, parse_mode="HTML")
            return
            
        send_telegram_message("✅ Tarama başarıyla tamamlandı!", chat_id)
        send_status_report(chat_id)
        send_review_list(chat_id)
        
    except Exception as e:
        send_telegram_message(f"❌ İşlem sırasında beklenmedik bir hata oluştu: {str(e)}", chat_id)
    finally:
        with PIPELINE_LOCK:
            PIPELINE_RUNNING = False

def run_scheduled_scan():
    threading.Thread(target=run_pipeline_worker_thread, args=(TELEGRAM_CHAT_ID,), daemon=True).start()

# Zamanlayıcı Kurulumu
def setup_scheduler():
    schedule_config = CONFIG.get("scan_schedule", {})
    if not schedule_config:
        print("Programlı tarama saatleri bulunamadı, zamanlayıcı devre dışı.")
        return
        
    for day_name in ["monday", "tuesday", "wednesday", "thursday", "friday"]:
        times = schedule_config.get(day_name, [])
        for t in times:
            if day_name == "monday":
                schedule.every().monday.at(t).do(run_scheduled_scan)
            elif day_name == "tuesday":
                schedule.every().tuesday.at(t).do(run_scheduled_scan)
            elif day_name == "wednesday":
                schedule.every().wednesday.at(t).do(run_scheduled_scan)
            elif day_name == "thursday":
                schedule.every().thursday.at(t).do(run_scheduled_scan)
            elif day_name == "friday":
                schedule.every().friday.at(t).do(run_scheduled_scan)
                
    print("📅 Otomatik tarama zamanlayıcısı hafta içi saatlerine kuruldu.")

# Telegram Mesaj Dinleme ve Yönlendirme
def handle_message(message):
    chat_id = message["chat"]["id"]
    text = message["text"].strip()
    
    if str(chat_id) != str(TELEGRAM_CHAT_ID):
        print(f"⚠️ Yetkisiz mesaj denemesi! Chat ID: {chat_id}, Mesaj: {text}")
        return
        
    parts = text.split()
    if not parts:
        return
        
    cmd = parts[0].lower()
    args = parts[1:]
    
    if cmd == "/start" or cmd == "/help":
        send_help_message(chat_id)
    elif cmd == "/scan":
        threading.Thread(target=run_pipeline_worker_thread, args=(chat_id,), daemon=True).start()
    elif cmd == "/review":
        send_review_list(chat_id)
    elif cmd == "/accept":
        if not args:
            send_telegram_message("⚠️ Kullanım: <code>/accept &lt;SYMBOL&gt;</code> veya <code>/accept ALL</code>", chat_id, parse_mode="HTML")
        else:
            success, msg = update_user_decision(args[0], "ACCEPT")
            send_telegram_message(msg, chat_id, parse_mode="HTML")
    elif cmd == "/reject":
        if not args:
            send_telegram_message("⚠️ Kullanım: <code>/reject &lt;SYMBOL&gt;</code> veya <code>/reject ALL</code>", chat_id, parse_mode="HTML")
        else:
            success, msg = update_user_decision(args[0], "REJECT")
            send_telegram_message(msg, chat_id, parse_mode="HTML")
    elif cmd == "/hold":
        if not args:
            send_telegram_message("⚠️ Kullanım: <code>/hold &lt;SYMBOL&gt;</code>", chat_id, parse_mode="HTML")
        else:
            success, msg = update_user_decision(args[0], "HOLD_REVIEW")
            send_telegram_message(msg, chat_id, parse_mode="HTML")
    elif cmd == "/status":
        send_status_report(chat_id)
    elif cmd == "/positions":
        send_positions_report(chat_id)
    elif cmd == "/report":
        send_master_report_file(chat_id)
    else:
        send_telegram_message("❓ Bilinmeyen komut. Yardım almak için <b>/help</b> yazabilirsiniz.", chat_id, parse_mode="HTML")

# Telegram Callback Query (Buton Tıklaması) İşleyici
def handle_callback_query(callback_query):
    query_id = callback_query["id"]
    chat_id = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]
    data = callback_query["data"]
    
    if str(chat_id) != str(TELEGRAM_CHAT_ID):
        answer_callback_query(query_id, "⚠️ Yetkisiz işlem!")
        return
        
    try:
        parts = data.split("_", 1)
        if len(parts) != 2:
            answer_callback_query(query_id, "⚠️ Geçersiz veri!")
            return
            
        action, symbol = parts
        decision_map = {
            "accept": "ACCEPT",
            "reject": "REJECT",
            "hold": "HOLD_REVIEW"
        }
        
        if action not in decision_map:
            answer_callback_query(query_id, "⚠️ Geçersiz işlem!")
            return
            
        decision = decision_map[action]
        success, msg = update_user_decision(symbol, decision)
        
        if success:
            answer_callback_query(query_id, f"✅ {symbol} kararı '{decision}' yapıldı.")
            original_text = callback_query["message"].get("caption") or callback_query["message"].get("text", "")
            
            action_text = "Bilinmiyor"
            if "Aksiyon:" in original_text:
                try:
                    action_text = original_text.split("Aksiyon:")[1].split()[0]
                except Exception:
                    pass
            
            updated_text = (
                f"🚨 <b>Sinyal Kararı Güncellendi: {symbol}</b>\n"
                f"• Aksiyon: <b>{action_text}</b>\n"
                f"• Kararınız: <b>{decision}</b>\n\n"
                f"✅ <i>İşlem başarıyla kaydedildi.</i>"
            )
            
            if "photo" in callback_query["message"] or callback_query["message"].get("caption"):
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageCaption"
                payload = {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "caption": updated_text,
                    "parse_mode": "HTML",
                    "reply_markup": json.dumps({"inline_keyboard": []})
                }
                requests.post(url, payload, timeout=15)
            else:
                edit_message_text(chat_id, message_id, updated_text, parse_mode="HTML")
        else:
            answer_callback_query(query_id, f"❌ Hata: {msg}")
            
    except Exception as e:
        print(f"Callback query işleme hatası: {e}")
        answer_callback_query(query_id, "❌ İşlem sırasında bir hata oluştu.")

# İnteraktif Telegram Sohbet Cockpit Modu
def run_interactive_cockpit():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("HATA: TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID .env dosyasında tanımlı olmalıdır!")
        sys.exit(1)
        
    setup_scheduler()
    
    offset = None
    consecutive_errors = 0
    print("🤖 Telegram Bot Cockpit aktif. Mesajlar dinleniyor...")
    
    send_telegram_message("🤖 <b>Kuantitatif Swing Trading Bot Cockpit aktif!</b>\nStratejileri test etmek ve yönetmek için <b>/help</b> yazabilirsiniz.", parse_mode="HTML")
    
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {"timeout": 10}
            if offset:
                params["offset"] = offset
                
            res = requests.get(url, params=params, timeout=15)
            if res.status_code == 200:
                consecutive_errors = 0
                data = res.json()
                if data.get("ok") and data.get("result"):
                    for update in data["result"]:
                        offset = update["update_id"] + 1
                        if "callback_query" in update:
                            handle_callback_query(update["callback_query"])
                        elif "message" in update:
                            message = update["message"]
                            if "text" in message:
                                handle_message(message)
                                
            # Zamanlayıcı tetikle
            schedule.run_pending()
            time.sleep(1)
        except KeyboardInterrupt:
            print("\nBot sonlandırıldı.")
            break
        except Exception as e:
            consecutive_errors += 1
            print(f"Poller Ana Döngü Hatası (Hata sayısı: {consecutive_errors}): {e}")
            sleep_time = min(60, 2 ** consecutive_errors)
            time.sleep(sleep_time)

# Ana Giriş Noktası (CLI ve Interactive Seçici)
def main():
    args = parse_args()
    global CONFIG, STOCK_SYMBOLS
    CONFIG = load_config()
    STOCK_SYMBOLS = CONFIG.get("stock_symbols", [])
    
    if args.mode == "status":
        run_status(args)
    elif args.mode == "local-paper":
        run_local_paper(args)
    elif args.mode == "execution-preview":
        run_execution_preview(args)
    elif args.mode == "pre-submit-check":
        run_pre_submit_check(args)
    elif args.mode == "alpaca-check":
        run_alpaca_check(args)
    elif args.mode == "backup":
        run_backup(args)
    elif args.mode == "interactive":
        run_interactive_cockpit()
    elif args.mode == "dss-report":
        run_dss_report(args)

if __name__ == "__main__":
    main()
