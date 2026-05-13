"""
scripts/log_weekly_actuals.py — Brief Part 5B / Phase 8

Compares last week's forecast against actuals from sales_history and writes
one row per SKU into the forecast_accuracy_log table. Intended to be run
weekly *before* the Monday pipeline (so retraining decisions consider it).

Idempotent: if a row already exists for (sku_id, week_start_date), it skips.
"""
import os
import sys
from datetime import datetime
import pandas as pd
from sqlalchemy import create_engine, text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
PROCESSED = os.path.join(ROOT, "data", "processed")


def main():
    forecasts_path = os.path.join(PROCESSED, "forecasts.csv")
    sales_path = os.path.join(DATA, "sales_history.csv")
    if not os.path.exists(forecasts_path) or not os.path.exists(sales_path):
        print("Required files missing (forecasts.csv or sales_history.csv).")
        sys.exit(1)

    forecasts = pd.read_csv(forecasts_path)
    sales = pd.read_csv(sales_path)
    forecasts["week_start_date"] = pd.to_datetime(forecasts["week_start_date"])
    sales["week_start_date"] = pd.to_datetime(sales["week_start_date"])

    last_data_week = sales["week_start_date"].max()
    target_week = forecasts[forecasts["week_start_date"] <= last_data_week]
    if len(target_week) == 0:
        print("No forecast week with corresponding actuals — nothing to log yet.")
        return
    target_date = target_week["week_start_date"].max()
    fc_for_week = forecasts[forecasts["week_start_date"] == target_date]
    actual_for_week = sales[sales["week_start_date"] == target_date].groupby("sku_id")["units_sold"].sum()
    print(f"[accuracy] Logging actuals for week {target_date.date()} ({len(fc_for_week)} SKUs)")

    db_uri = os.environ.get("DATABASE_URL", f"sqlite:///{os.path.join(ROOT, 'sunrise.db')}")
    if db_uri.startswith("postgres://"):
        db_uri = "postgresql://" + db_uri[len("postgres://"):]
    eng = create_engine(db_uri, future=True)
    inserted = 0
    skipped = 0
    with eng.begin() as conn:
        sku_id_map = {}
        for code, sid in conn.execute(text("SELECT sku_code, id FROM skus")).fetchall():
            sku_id_map[code] = sid
        for _, row in fc_for_week.iterrows():
            code = str(row["sku_id"])
            sid = sku_id_map.get(code)
            if not sid:
                continue
            forecasted = int(row["forecasted_units"])
            actual = int(actual_for_week.get(code, 0))
            mape = abs(actual - forecasted) / max(actual, 1) * 100
            existing = conn.execute(text(
                "SELECT 1 FROM forecast_accuracy_log "
                "WHERE sku_id = :sid AND week_start_date = :w LIMIT 1"
            ), {"sid": sid, "w": target_date.date().isoformat()}).fetchone()
            if existing:
                skipped += 1
                continue
            conn.execute(text(
                "INSERT INTO forecast_accuracy_log "
                "(id, week_start_date, sku_id, forecasted_units, actual_units, mape_contribution, store_id) "
                "VALUES (:id, :w, :sid, :f, :a, :m, :s)"
            ), {
                "id": __import__('uuid').uuid4().hex,
                "w": target_date.date().isoformat(),
                "sid": sid, "f": forecasted, "a": actual,
                "m": round(mape, 4), "s": "store-pune-001"
            })
            inserted += 1
    print(f"[accuracy] Inserted {inserted} rows, skipped {skipped} duplicates.")


if __name__ == "__main__":
    main()
