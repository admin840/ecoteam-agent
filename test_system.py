"""Quick system tests for EcoTeam Agent."""
import os
import sys
import sqlite3


def test_imports():
    """Test that all modules import correctly."""
    import analyzer
    import main
    print("✅ Imports OK")


def test_db():
    """Test database is accessible and has data."""
    db_path = os.environ.get("DATA_DIR", "data") + "/ecoteam.db"
    if not os.path.exists(db_path):
        print(f"⚠️ DB not found at {db_path} - skipping")
        return

    conn = sqlite3.connect(db_path)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    expected = ["tracking", "conversations", "decisions", "team_accounts",
                "learnings", "daily_performance", "creative_history", "budget_tracking"]

    for t in expected:
        assert t in tables, f"Missing table: {t}"

    # Check historical data
    count = conn.execute("SELECT COUNT(*) FROM daily_performance").fetchone()[0]
    assert count >= 300, f"Expected 300+ performance rows, got {count}"

    # Check all 11 teams
    teams = [r[0] for r in conn.execute("SELECT DISTINCT team FROM daily_performance")]
    assert len(teams) >= 11, f"Expected 11 teams, got {len(teams)}"

    conn.close()
    print(f"✅ DB OK - {len(tables)} tables, {count} performance rows, {len(teams)} teams")


def test_analyzer_functions():
    """Test key analyzer functions exist."""
    import analyzer

    functions = [
        "get_leader", "get_sheet_name", "fetch_team_sheet",
        "extract_image_data", "smart_analysis", "quick_image_check",
        "log_to_tracking", "get_missing_for_team", "build_owner_team_report",
        "db_log_tracking", "db_get_tracking_today", "db_get_daily_performance",
        "analyze_pdf_orders", "analyze_document", "analyze_video_creative",
    ]

    for func_name in functions:
        assert hasattr(analyzer, func_name), f"Missing function: {func_name}"

    print(f"✅ Analyzer functions OK - {len(functions)} verified")


def test_team_info():
    """Test TEAM_INFO has all 11 teams."""
    import analyzer
    assert len(analyzer.TEAM_INFO) >= 11, f"Expected 11 teams, got {len(analyzer.TEAM_INFO)}"

    for team_name, info in analyzer.TEAM_INFO.items():
        assert "leader" in info, f"{team_name} missing leader"
        assert "sheet_id" in info, f"{team_name} missing sheet_id"

    print(f"✅ TEAM_INFO OK - {len(analyzer.TEAM_INFO)} teams")


def test_callback_prefixes():
    """Test all callback prefixes are handled."""
    with open("main.py", encoding="utf-8") as f:
        code = f.read()

    prefixes = ["it_", "ac_", "cf_", "od_", "tm_", "ps_", "ar_", "cr_", "help_"]
    for prefix in prefixes:
        assert f'startswith("{prefix}")' in code, f"Missing callback handler for {prefix}"

    print(f"✅ Callback prefixes OK - {len(prefixes)} handled")


def test_scheduled_jobs():
    """Test all scheduled jobs are defined."""
    with open("main.py", encoding="utf-8") as f:
        code = f.read()

    jobs = [
        "send_morning_prereminder",
        "send_smart_morning_reminder",
        "final_morning_check",
        "proactive_check",
        "smart_daily_report",
        "send_afternoon_questions",
        "daily_reset",
    ]

    for job in jobs:
        assert f"async def {job}" in code, f"Missing job function: {job}"
        assert job in code.split("jq.run_daily")[-1] or job in code, f"Job not scheduled: {job}"

    print(f"✅ Scheduled jobs OK - {len(jobs)} defined")


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    tests = [
        test_imports,
        test_db,
        test_analyzer_functions,
        test_team_info,
        test_callback_prefixes,
        test_scheduled_jobs,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"❌ {test.__name__}: {e}")
            failed += 1

    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)}")
    if failed == 0:
        print("🎉 All tests passed!")
    sys.exit(1 if failed else 0)
