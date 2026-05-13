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
    """Phase 4 fix: Validate and copy CSV files using ingestion firewall."""
    root = get_project_root()
    data_dir = os.path.join(root, "data")
    parent_dir = os.path.dirname(root)
    needed = ["sales_history.csv","inventory_snapshot.csv","sku_master.csv",
              "outlet_master.csv","promotions_calendar.csv","festive_calendar.csv"]
    import shutil
    try:
        from ingestion import validate_sales_upload
    except ImportError:
        validate_sales_upload = None

    for f in needed:
        dst = os.path.join(data_dir, f)
        if not os.path.exists(dst):
            src = os.path.join(parent_dir, f)
            if os.path.exists(src):
                # Run ingestion firewall on sales data
                if validate_sales_upload and f == "sales_history.csv":
                    is_valid, cleaned_df, report = validate_sales_upload(src)
                    if is_valid and cleaned_df is not None:
                        cleaned_df.to_csv(dst, index=False)
                        print(f"  Validated & copied {f} ({report.get('accepted',0)} rows accepted)")
                    else:
                        print(f"  WARNING: {f} failed validation: {report.get('errors',[])}")
                        shutil.copy2(src, dst)  # Copy anyway but log warning
                else:
                    shutil.copy2(src, dst)
                    print(f"  Copied {f}")

def export_db_to_csv_standalone(logger):
    """Brief C8: export DB tables to CSV without depending on Flask context.
    Uses raw SQLAlchemy so it works whether called from Flask, scheduler,
    or `python pipeline.py`."""
    try:
        from sqlalchemy import create_engine, text
        import pandas as pd
        root = get_project_root()
        db_uri = os.environ.get("DATABASE_URL",
                                f"sqlite:///{os.path.join(root, 'sunrise.db')}")
        if db_uri.startswith("postgres://"):
            db_uri = "postgresql://" + db_uri[len("postgres://"):]
        eng = create_engine(db_uri, future=True)
        data_dir = os.path.join(root, "data")
        with eng.connect() as conn:
            try:
                rows = conn.execute(text(
                    "SELECT sku_code, product_name, brand, category, unit_price, "
                    "cost_price, shelf_life_days, moq_from_supplier, supplier_lead_time_days "
                    "FROM skus"
                )).fetchall()
                if rows:
                    df = pd.DataFrame(rows, columns=[
                        "sku_id", "product_name", "brand", "category",
                        "unit_price", "cost_price", "shelf_life_days",
                        "moq_from_supplier", "supplier_lead_time_days"])
                    df.insert(4, "subcategory", "")
                    df.to_csv(os.path.join(data_dir, "sku_master.csv"), index=False)
                    logger.info(f"  exported {len(df)} SKUs DB → sku_master.csv")
            except Exception as e:
                logger.warning(f"  SKU export skipped: {e}")
            try:
                rows = conn.execute(text(
                    "SELECT outlet_code, channel, city, area FROM outlets"
                )).fetchall()
                if rows:
                    df = pd.DataFrame(rows, columns=["outlet_id", "channel", "city", "area"])
                    df["outlet_type"] = df["channel"]
                    existing_path = os.path.join(data_dir, "outlet_master.csv")
                    if os.path.exists(existing_path):
                        try:
                            existing = pd.read_csv(existing_path)
                            if "outlet_name" in existing.columns:
                                m = dict(zip(existing["outlet_id"], existing["outlet_name"]))
                                df["outlet_name"] = df["outlet_id"].map(m).fillna("")
                            else:
                                df["outlet_name"] = ""
                        except Exception:
                            df["outlet_name"] = ""
                    else:
                        df["outlet_name"] = ""
                    df = df[["outlet_id", "outlet_name", "outlet_type", "city", "area", "channel"]]
                    df.to_csv(existing_path, index=False)
                    logger.info(f"  exported {len(df)} outlets DB → outlet_master.csv")
            except Exception as e:
                logger.warning(f"  Outlet export skipped: {e}")
    except Exception as e:
        logger.warning(f"DB → CSV export skipped (DB unavailable: {e})")


def run_pipeline(progress_cb=None):
    """Run the 6-step pipeline. progress_cb(step_index, step_name) is invoked
    before each step so the caller can update DB-backed status (Brief Phase 7).
    """
    logger = setup_logging()
    logger.info("=" * 60)
    logger.info("PIPELINE START")
    root = get_project_root()
    os.makedirs(os.path.join(root, "data", "processed"), exist_ok=True)
    if progress_cb:
        try: progress_cb(0, "export_db_to_csv")
        except Exception: pass
    export_db_to_csv_standalone(logger)
    if progress_cb:
        try: progress_cb(0, "copy_source_data")
        except Exception: pass
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
    for idx, (module_name, desc) in enumerate(steps, start=1):
        if progress_cb:
            try: progress_cb(idx, desc)
            except Exception: pass
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
