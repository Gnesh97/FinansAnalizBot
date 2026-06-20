from pathlib import Path
import sys
# Add parent directory to path if needed
sys.path.append(str(Path(__file__).resolve().parent))

try:
    from runner import run_strategy_experiments
    from universe_scanner import VALIDATION_EXPERIMENTS
except ImportError:
    from .runner import run_strategy_experiments
    from .universe_scanner import VALIDATION_EXPERIMENTS

def main():
    data_folder = Path(__file__).resolve().parent / "hisseler"
    
    smoke_experiments = [e for e in VALIDATION_EXPERIMENTS if e["name"] == "rs_m63_sma100"]
    
    print("--- Running Smoke Test ---")
    run_strategy_experiments(
        str(data_folder),
        experiments=smoke_experiments,
        start_date="2020-07-01",
        end_date="2021-01-01",
        allowed_symbols=["AAPL", "MSFT", "QQQ", "SPY"],
        run_profile="fast",
        quick_mode=True,
        compact_report=True,
        write_experiment_detail_files=False,
    )

if __name__ == "__main__":
    main()
