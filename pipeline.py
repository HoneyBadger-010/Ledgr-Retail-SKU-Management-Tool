"""
pipeline.py — Runs all 6 backend scripts in sequence.
Logs each step. Exposes run_pipeline() for Streamlit.
"""
import os, sys, time, logging
from datetime import datetime

def get_project_root():
    return os.path.dirname(os.path.abspath(__file__))

def setup_logging():
    root = get_project_root()
    log_dir = os.path.join(root, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "pipeline_runs.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[logging.FileHandler(log_file, encoding='utf-8'), logging.StreamHandler()],
        force=True
    )
    return logging.getLogger("pipeline")

def copy_source_data():
    """Copy CSV files from parent directory if not already present."""
    root = get_project_root()
    data_dir = os.path.join(root, "data")
    parent_dir = os.path.dirname(root)
    needed = ["sales_history.csv","inventory_snapshot.csv","sku_master.csv",
              "outlet_master.csv","promotions_calendar.csv","festive_calendar.csv"]
    import shutil
    for f in needed:
        dst = os.path.join(data_dir, f)
        if not os.path.exists(dst):
            src = os.path.join(parent_dir, f)
            if os.path.exists(src):
                shutil.copy2(src, dst)
                print(f"  Copied {f}")

def run_pipeline():
    logger = setup_logging()
    logger.info("=" * 60)
    logger.info("PIPELINE START")
    root = get_project_root()
    # Ensure data directory exists
    os.makedirs(os.path.join(root, "data", "processed"), exist_ok=True)
    # Copy source data if needed
    copy_source_data()
    # Add project root and backend to path
    sys.path.insert(0, root)
    sys.path.insert(0, os.path.join(root, "backend"))
    steps = [
        ("1_clean_data", "Data Classification"),
        ("2_forecast", "Demand Forecasting"),
        ("3_retrospective", "Diwali 2023 Retrospective"),
        ("4_reorder_engine", "Reorder Optimization"),
        ("5_sku_classifier", "SKU Classification"),
        ("6_report_generator", "Monday Report"),
    ]
    results = {}
    for module_name, desc in steps:
        logger.info(f"Step: {desc} ({module_name})")
        t0 = time.time()
        try:
            mod = __import__(module_name)
            mod.run()
            elapsed = time.time() - t0
            logger.info(f"  [OK] {desc} completed in {elapsed:.1f}s")
            results[module_name] = {"status": "success", "time": round(elapsed, 1)}
        except Exception as e:
            elapsed = time.time() - t0
            logger.error(f"  [FAIL] {desc} FAILED after {elapsed:.1f}s: {e}")
            results[module_name] = {"status": "failed", "error": str(e), "time": round(elapsed, 1)}
            import traceback
            traceback.print_exc()
    total = sum(r["time"] for r in results.values())
    success = sum(1 for r in results.values() if r["status"] == "success")
    logger.info(f"PIPELINE COMPLETE: {success}/{len(steps)} steps succeeded in {total:.1f}s")
    return results

if __name__ == "__main__":
    run_pipeline()
