from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parent))

try:
    from runner import run_strategy_experiments
    from universe_scanner import VALIDATION_EXPERIMENTS
except ImportError:
    from .runner import run_strategy_experiments
    from .universe_scanner import VALIDATION_EXPERIMENTS

def main():
    data_folder = Path(__file__).resolve().parent / "hisseler"
    
    baseline_names = [
        "rs_m63_sma100",
        "rs_m63_no_regime",
        "dm_m63_top20",
        "donchian_20_10_top20",
        "mast_sma100_slope20_top20"
    ]
    
    baseline_experiments = [e for e in VALIDATION_EXPERIMENTS if e["name"] in baseline_names]
    
    print("--- Running Full Baseline ---")
    run_strategy_experiments(
        str(data_folder),
        experiments=baseline_experiments,
        start_date="2020-07-01",
        run_profile="fast",
        quick_mode=True,
        compact_report=True,
        write_experiment_detail_files=False,
    )

if __name__ == "__main__":
    main()
