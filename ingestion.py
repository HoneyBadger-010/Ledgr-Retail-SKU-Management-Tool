"""
ingestion.py — Data Ingestion & Validation Layer (Brief Part 2D / Phase 4)

Validates incoming weekly sales uploads before they enter the pipeline.
Checks:
  1. Required columns present
  2. SKU-outlet pairs exist in master data
  3. Numeric ranges (no negative units_sold)
  4. Date format validation
  5. Duplicate detection (same SKU-outlet-week)
  6. Row count drift detection (>15% drop from 4-week average = alert)

Output: data_quality_log entry + cleaned file saved to data/uploads/
"""
import os
import pandas as pd
import json
from datetime import datetime

def get_project_root():
    return os.path.dirname(os.path.abspath(__file__))


def validate_sales_upload(filepath, sku_master_path=None, outlet_master_path=None):
    """
    Validate a sales CSV upload.
    Returns: (is_valid, cleaned_df, quality_report)
    """
    root = get_project_root()
    if not sku_master_path:
        sku_master_path = os.path.join(root, "data", "sku_master.csv")
    if not outlet_master_path:
        outlet_master_path = os.path.join(root, "data", "outlet_master.csv")

    report = {
        "filename": os.path.basename(filepath),
        "upload_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "rows_received": 0,
        "rows_accepted": 0,
        "rows_rejected": 0,
        "rejection_reasons": [],
        "warnings": [],
        "is_valid": False
    }

    try:
        df = pd.read_csv(filepath)
        report["rows_received"] = len(df)
    except Exception as e:
        report["rejection_reasons"].append({"code": "PARSE_ERROR", "count": 1, "detail": str(e)})
        return False, pd.DataFrame(), report

    # ── Check 1: Required columns ──
    required_cols = ["sku_id", "outlet_id", "week_start_date", "units_sold"]
    optional_cols = ["returns", "promotional_flag"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        report["rejection_reasons"].append({
            "code": "MISSING_COLUMNS",
            "count": 1,
            "detail": f"Missing required columns: {', '.join(missing)}"
        })
        return False, pd.DataFrame(), report

    # ── Check 2: Date format ──
    try:
        df["week_start_date"] = pd.to_datetime(df["week_start_date"])
    except Exception:
        report["rejection_reasons"].append({
            "code": "INVALID_DATE_FORMAT",
            "count": len(df),
            "detail": "week_start_date column could not be parsed as dates"
        })
        return False, pd.DataFrame(), report

    # ── Check 3: Numeric validation ──
    rejected_mask = pd.Series([False] * len(df))

    # Negative units_sold
    neg_units = df["units_sold"] < 0
    if neg_units.sum() > 0:
        report["rejection_reasons"].append({
            "code": "NEGATIVE_UNITS",
            "count": int(neg_units.sum()),
            "detail": "Rows with negative units_sold"
        })
        rejected_mask |= neg_units

    # NaN units_sold
    null_units = df["units_sold"].isna()
    if null_units.sum() > 0:
        report["rejection_reasons"].append({
            "code": "NULL_UNITS",
            "count": int(null_units.sum()),
            "detail": "Rows with null units_sold"
        })
        rejected_mask |= null_units

    # Unreasonably high units (>10000 per outlet per week — likely error)
    if "units_sold" in df.columns:
        high_units = df["units_sold"] > 10000
        if high_units.sum() > 0:
            report["warnings"].append({
                "code": "SUSPICIOUSLY_HIGH_UNITS",
                "count": int(high_units.sum()),
                "detail": "Rows with units_sold > 10,000 (flagged for review)"
            })

    # ── Check 4: SKU-outlet validation ──
    if os.path.exists(sku_master_path):
        sku_master = pd.read_csv(sku_master_path)
        valid_skus = set(sku_master["sku_id"].values)
        unknown_skus = ~df["sku_id"].isin(valid_skus)
        if unknown_skus.sum() > 0:
            bad_skus = df.loc[unknown_skus, "sku_id"].unique().tolist()
            report["rejection_reasons"].append({
                "code": "UNKNOWN_SKU",
                "count": int(unknown_skus.sum()),
                "detail": f"Unknown SKU IDs: {', '.join(bad_skus[:10])}"
            })
            rejected_mask |= unknown_skus

    if os.path.exists(outlet_master_path):
        outlet_master = pd.read_csv(outlet_master_path)
        valid_outlets = set(outlet_master["outlet_id"].values)
        unknown_outlets = ~df["outlet_id"].isin(valid_outlets)
        if unknown_outlets.sum() > 0:
            bad_outlets = df.loc[unknown_outlets, "outlet_id"].unique().tolist()
            report["rejection_reasons"].append({
                "code": "UNKNOWN_OUTLET",
                "count": int(unknown_outlets.sum()),
                "detail": f"Unknown outlet IDs: {', '.join(str(o) for o in bad_outlets[:10])}"
            })
            rejected_mask |= unknown_outlets

    # ── Check 5: Duplicate detection ──
    dup_cols = ["sku_id", "outlet_id", "week_start_date"]
    duplicates = df.duplicated(subset=dup_cols, keep="first")
    if duplicates.sum() > 0:
        report["rejection_reasons"].append({
            "code": "DUPLICATE_ROWS",
            "count": int(duplicates.sum()),
            "detail": "Duplicate SKU-outlet-week combinations"
        })
        rejected_mask |= duplicates

    # ── Separate accepted and rejected ──
    accepted_df = df[~rejected_mask].copy()
    rejected_df = df[rejected_mask].copy()

    report["rows_accepted"] = len(accepted_df)
    report["rows_rejected"] = len(rejected_df)
    report["acceptance_rate"] = round(len(accepted_df) / max(len(df), 1) * 100, 1)
    report["is_valid"] = len(accepted_df) > 0

    # ── Check 6: Row count drift detection ──
    # Compare against previous uploads
    quality_log_path = os.path.join(root, "data", "processed", "data_quality_history.json")
    if os.path.exists(quality_log_path):
        with open(quality_log_path) as f:
            history = json.load(f)
        if len(history) >= 4:
            recent_4 = [h["rows_accepted"] for h in history[-4:]]
            avg_4w = sum(recent_4) / len(recent_4)
            if avg_4w > 0:
                drift_pct = (avg_4w - len(accepted_df)) / avg_4w * 100
                if drift_pct > 15:
                    report["warnings"].append({
                        "code": "ROW_COUNT_DRIFT",
                        "count": 1,
                        "detail": f"Row count {drift_pct:.1f}% below 4-week average ({avg_4w:.0f}). Possible data collection issue."
                    })

    # ── Save quality log ──
    os.makedirs(os.path.join(root, "data", "processed"), exist_ok=True)
    if os.path.exists(quality_log_path):
        with open(quality_log_path) as f:
            history = json.load(f)
    else:
        history = []
    history.append({
        "date": report["upload_date"],
        "filename": report["filename"],
        "rows_received": report["rows_received"],
        "rows_accepted": report["rows_accepted"],
        "rows_rejected": report["rows_rejected"],
        "acceptance_rate": report["acceptance_rate"]
    })
    with open(quality_log_path, "w") as f:
        json.dump(history, f, indent=2)

    return report["is_valid"], accepted_df, report


def save_validated_upload(df, filename=None):
    """Save validated data to the uploads archive."""
    root = get_project_root()
    uploads_dir = os.path.join(root, "data", "uploads")
    os.makedirs(uploads_dir, exist_ok=True)

    if not filename:
        filename = f"validated_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    path = os.path.join(uploads_dir, filename)
    df.to_csv(path, index=False)
    return path


if __name__ == "__main__":
    # Test with existing sales data
    import sys
    root = os.path.dirname(os.path.abspath(__file__))
    test_file = os.path.join(root, "data", "weekly_sales.csv")
    if os.path.exists(test_file):
        is_valid, cleaned, report = validate_sales_upload(test_file)
        print(f"Valid: {is_valid}")
        print(f"Accepted: {report['rows_accepted']}/{report['rows_received']} ({report.get('acceptance_rate', 0)}%)")
        print(f"Rejected: {report['rows_rejected']}")
        for r in report["rejection_reasons"]:
            print(f"  - {r['code']}: {r['count']} ({r['detail']})")
        for w in report["warnings"]:
            print(f"  ⚠ {w['code']}: {w['detail']}")
    else:
        print(f"Test file not found: {test_file}")
