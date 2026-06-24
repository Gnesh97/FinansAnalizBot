import runpy
import sys
from pathlib import Path


if __name__ == "__main__":
    if "--alpaca-paper-submit" in sys.argv:
        import os
        script_path = Path(__file__).resolve().parent / "testdata" / "universe_scanner.py"
        sys.path.insert(0, str(script_path.parent))
        
        make_chatgpt_report = "--make-chatgpt-report" in sys.argv
        paper_submit_enabled = "--paper-submit-enabled" in sys.argv or os.environ.get("PAPER_BROKER_SUBMIT_ENABLED") == "1"
        
        confirm_token = None
        max_submit_orders = 1
        max_submit_notional = 25
        
        if os.environ.get("MAX_SUBMIT_ORDERS"):
            try:
                max_submit_orders = int(os.environ.get("MAX_SUBMIT_ORDERS"))
            except ValueError:
                pass
        if os.environ.get("MAX_SUBMIT_NOTIONAL"):
            try:
                max_submit_notional = float(os.environ.get("MAX_SUBMIT_NOTIONAL"))
            except ValueError:
                pass

        for idx, arg in enumerate(sys.argv):
            if arg.startswith("--confirm-token="):
                confirm_token = arg.split("=", 1)[1]
            elif arg == "--confirm-token" and idx + 1 < len(sys.argv):
                confirm_token = sys.argv[idx + 1]
            elif arg.startswith("--max-submit-orders="):
                try:
                    max_submit_orders = int(arg.split("=", 1)[1])
                except ValueError:
                    pass
            elif arg == "--max-submit-orders" and idx + 1 < len(sys.argv):
                try:
                    max_submit_orders = int(sys.argv[idx + 1])
                except ValueError:
                    pass
            elif arg.startswith("--max-submit-notional="):
                try:
                    max_submit_notional = float(arg.split("=", 1)[1])
                except ValueError:
                    pass
            elif arg == "--max-submit-notional" and idx + 1 < len(sys.argv):
                try:
                    max_submit_notional = float(sys.argv[idx + 1])
                except ValueError:
                    pass
                
        from execution_engine import run_alpaca_paper_submit
        run_alpaca_paper_submit(
            latest_dir=str(script_path.parent / "reports" / "latest"),
            paper_submit_enabled=paper_submit_enabled,
            confirm_token=confirm_token,
            max_submit_orders=max_submit_orders,
            max_submit_notional=max_submit_notional,
            make_chatgpt_report=make_chatgpt_report,
        )
        sys.exit(0)

    script_path = Path(__file__).resolve().parent / "testdata" / "universe_scanner.py"
    sys.path.insert(0, str(script_path.parent))
    runpy.run_path(str(script_path), run_name="__main__")
