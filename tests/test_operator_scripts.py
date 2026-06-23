import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _script_text(name):
    return (ROOT / "scripts" / name).read_text(encoding="utf-8", errors="replace")


class OperatorScriptsTest(unittest.TestCase):
    def test_daily_pipeline_runs_pre_submit_safety_step(self):
        text = _script_text("run_daily_paper_pipeline.bat")
        self.assertIn("python universe_scanner.py --execution-preview --pre-submit-check --make-chatgpt-report", text)
        self.assertIn("Pre-submit readiness report: reports\\latest\\pre_submit_readiness_report.md", text)
        self.assertIn("[4/4] Done.", text)

    def test_paper_report_runs_pre_submit_safety_step(self):
        text = _script_text("run_paper_report.bat")
        self.assertIn("python universe_scanner.py --reports basic --parallel --workers auto --paper-report --make-chatgpt-report", text)
        self.assertIn("python universe_scanner.py --execution-preview --pre-submit-check --make-chatgpt-report", text)
        self.assertIn("Pre-submit readiness report: reports\\latest\\pre_submit_readiness_report.md", text)

    def test_backup_includes_execution_safety_artifacts(self):
        text = _script_text("backup_latest_reports.bat")
        required = [
            "reports\\latest\\order_intents.csv",
            "reports\\latest\\order_risk_check_report.csv",
            "reports\\latest\\order_risk_check_report.md",
            "reports\\latest\\execution_preview_report.csv",
            "reports\\latest\\execution_preview_report.md",
            "reports\\latest\\execution_audit_log.csv",
            "reports\\latest\\pre_submit_readiness_report.csv",
            "reports\\latest\\pre_submit_readiness_report.md",
            "reports\\latest\\short_readiness_report.csv",
            "reports\\latest\\short_readiness_report.md",
        ]
        for artifact in required:
            self.assertIn(artifact, text)

    def test_backup_includes_operational_health_artifacts(self):
        text = _script_text("backup_latest_reports.bat")
        required = [
            "reports\\latest\\daily_preflight_report.md",
            "reports\\latest\\data_precheck_report.md",
            "reports\\latest\\data_policy_decisions.csv",
            "reports\\latest\\data_policy_review_report.md",
            "reports\\latest\\data_policy_review_evidence.csv",
            "reports\\latest\\regression_guard_report.md",
            "reports\\latest\\pipeline_health_log.csv",
            "reports\\latest\\run_manifest.json",
            "reports\\latest\\test_run_report.md",
            "reports\\latest\\paper_state_validation_report.md",
            "reports\\latest\\report_schema_validation_report.md",
            "reports\\latest\\project_status_detailed_report.md",
        ]
        for artifact in required:
            self.assertIn(artifact, text)

    def test_data_warning_refresh_targets_warning_symbols_and_rebuilds_reports(self):
        text = _script_text("run_data_warning_refresh.bat")
        self.assertIn("python vericekici_updated.py --symbols DOW BK PARA PSA", text)
        self.assertIn("--overwrite", text)
        self.assertIn("python universe_scanner.py --reports basic --parallel --workers auto --paper-report --make-chatgpt-report", text)
        self.assertIn("python universe_scanner.py --execution-preview --pre-submit-check --make-chatgpt-report", text)
        self.assertIn("Data precheck report: reports\\latest\\data_precheck_report.md", text)

    def test_run_tests_uses_unittest_suite(self):
        text = _script_text("run_tests.bat")
        wrapper = (ROOT / "testdata" / "run_test_suite.py").read_text(encoding="utf-8")
        self.assertIn("python testdata\\run_test_suite.py", text)
        self.assertIn('"-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"', wrapper)
        self.assertIn("test_run_report.md", wrapper)
        self.assertNotIn("pytest", text.lower())
        self.assertIn('"testdata/execution_engine.py"', wrapper)
        self.assertIn('"testdata/alpaca_paper_broker.py"', wrapper)
        self.assertIn('"testdata/data_policy_review.py"', wrapper)

    def test_data_policy_review_helper_points_to_operator_files(self):
        text = _script_text("run_data_policy_review.bat")
        self.assertIn("python data_policy_review.py", text)
        self.assertIn("reports\\latest\\data_policy_decisions.csv", text)
        self.assertIn("reports\\latest\\data_policy_review_report.md", text)
        self.assertIn("reports\\latest\\data_policy_review_evidence.csv", text)
        self.assertIn("reports\\latest\\pre_submit_readiness_report.md", text)


if __name__ == "__main__":
    unittest.main()
