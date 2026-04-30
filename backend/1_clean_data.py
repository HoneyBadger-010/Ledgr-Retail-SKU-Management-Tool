"""
backend/1_clean_data.py -- True Zero vs Missing Data Classifier

Reconstructs the FULL grid (week x SKU x outlet) and classifies every
combination as: observed | true_zero | missing_data | stockout_gap | uncertain
"""
import pandas as pd
import numpy as np
import json
import os

def get_project_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def run():
    root = get_project_root()
    data_dir = os.path.join(root, "data")
    processed_dir = os.path.join(root, "data", "processed")
    docs_dir = os.path.join(root, "docs")
    os.makedirs(processed_dir, exist_ok=True)
    os.makedirs(docs_dir, exist_ok=True)

    print("[1_clean_data] Loading data...")
    sales = pd.read_csv(os.path.join(data_dir, "sales_history.csv"))
    inventory = pd.read_csv(os.path.join(data_dir, "inventory_snapshot.csv"))
    sales["week_start_date"] = pd.to_datetime(sales["week_start_date"])

    # ── STEP 0: Reconstruct full grid ──
    print("[1_clean_data] Step 0: Reconstructing full week x SKU x outlet grid...")
    all_weeks = sorted(sales["week_start_date"].unique())
    all_skus = sorted(sales["sku_id"].unique())
    all_outlets = sorted(sales["outlet_id"].unique())

    print(f"  Grid dimensions: {len(all_weeks)} weeks x {len(all_skus)} SKUs x {len(all_outlets)} outlets")
    expected_rows = len(all_weeks) * len(all_skus) * len(all_outlets)
    print(f"  Expected full grid: {expected_rows:,} rows")
    print(f"  Observed rows in CSV: {len(sales):,}")

    # Build full grid using MultiIndex for memory efficiency
    full_idx = pd.MultiIndex.from_product(
        [all_weeks, all_skus, all_outlets],
        names=["week_start_date", "sku_id", "outlet_id"]
    )
    full_grid = pd.DataFrame(index=full_idx).reset_index()

    # Left join observed sales onto the full grid
    sales_key_cols = ["week_start_date", "sku_id", "outlet_id"]
    value_cols = [c for c in sales.columns if c not in sales_key_cols]

    merged = full_grid.merge(
        sales[sales_key_cols + value_cols],
        on=sales_key_cols,
        how="left",
        indicator=True
    )

    # Mark which rows were originally observed vs reconstructed
    merged["was_observed"] = merged["_merge"] == "both"
    merged.drop(columns=["_merge"], inplace=True)

    # Fill NaN for reconstructed rows
    if "units_sold" in merged.columns:
        merged["units_sold"] = merged["units_sold"].fillna(0).astype(int)
    if "returns" in merged.columns:
        merged["returns"] = merged["returns"].fillna(0).astype(int)
    if "promotional_flag" in merged.columns:
        merged["promotional_flag"] = merged["promotional_flag"].fillna(0).astype(int)

    reconstructed_count = (~merged["was_observed"]).sum()
    print(f"  Reconstructed missing rows: {reconstructed_count:,}")

    # Initialize classification
    # Observed rows with sales > 0 -> "observed"
    # Everything else starts as unclassified
    merged["row_classification"] = "unclassified"
    merged.loc[merged["was_observed"] & (merged["units_sold"] > 0), "row_classification"] = "observed"

    # ── STEP 1: Outlet weekly reporting check ──
    print("[1_clean_data] Step 1: Outlet weekly reporting check...")
    # If outlet has ZERO total sales across ALL SKUs in a week -> non-reporting
    outlet_week_total = merged.groupby(["outlet_id", "week_start_date"])["units_sold"].sum().reset_index()
    outlet_week_total.rename(columns={"units_sold": "outlet_week_total"}, inplace=True)
    non_reporting = outlet_week_total[outlet_week_total["outlet_week_total"] == 0][
        ["outlet_id", "week_start_date"]
    ]

    if len(non_reporting) > 0:
        nr_set = set(zip(non_reporting["outlet_id"], non_reporting["week_start_date"]))
        mask_nr = merged.apply(lambda r: (r["outlet_id"], r["week_start_date"]) in nr_set, axis=1)
        # Only reclassify unclassified rows
        merged.loc[mask_nr & (merged["row_classification"] == "unclassified"), "row_classification"] = "missing_data"

    step1_count = (merged["row_classification"] == "missing_data").sum()
    print(f"  Step 1: {step1_count:,} rows -> missing_data (non-reporting outlets)")

    # ── STEP 2: Stockout gap detection ──
    print("[1_clean_data] Step 2: Stockout gap detection...")
    # SKUs with warehouse_stock == 0 or very low
    zero_stock_skus = inventory[inventory["warehouse_stock"] <= 20]["sku_id"].tolist()

    for sku in zero_stock_skus:
        mask = (
            (merged["sku_id"] == sku) &
            (merged["units_sold"] == 0) &
            (merged["row_classification"] == "unclassified")
        )
        merged.loc[mask, "row_classification"] = "stockout_gap"

    step2_count = (merged["row_classification"] == "stockout_gap").sum()
    print(f"  Step 2: {step2_count:,} rows -> stockout_gap")

    # ── STEP 3: Channel frequency baseline ──
    print("[1_clean_data] Step 3: SKU-outlet sell frequency...")

    # Compute sell frequency per outlet-SKU pair (using only non-missing_data weeks)
    valid = merged[merged["row_classification"] != "missing_data"]
    outlet_sku_stats = valid.groupby(["outlet_id", "sku_id"]).agg(
        weeks_with_sales=("units_sold", lambda x: (x > 0).sum()),
        total_weeks=("units_sold", "count")
    ).reset_index()
    outlet_sku_stats["sell_frequency"] = (
        outlet_sku_stats["weeks_with_sales"] /
        outlet_sku_stats["total_weeks"].clip(lower=1)
    )

    # Merge sell_frequency back
    merged = merged.merge(
        outlet_sku_stats[["outlet_id", "sku_id", "sell_frequency"]],
        on=["outlet_id", "sku_id"],
        how="left"
    )
    merged["sell_frequency"] = merged["sell_frequency"].fillna(0)

    # Classify remaining unclassified rows
    still_unclassified = merged["row_classification"] == "unclassified"

    # High frequency seller with zero -> true_zero (product is usually sold here)
    high_freq = still_unclassified & (merged["sell_frequency"] > 0.6)
    merged.loc[high_freq, "row_classification"] = "true_zero"

    # Low frequency seller with zero -> missing_data (product rarely sold here)
    low_freq = still_unclassified & (merged["sell_frequency"] < 0.2)
    merged.loc[low_freq, "row_classification"] = "missing_data"

    # Mid frequency -> uncertain (treat conservatively as true_zero for forecasting)
    mid_freq = still_unclassified & (merged["sell_frequency"] >= 0.2) & (merged["sell_frequency"] <= 0.6)
    merged.loc[mid_freq, "row_classification"] = "uncertain"

    # Any remaining unclassified -> true_zero (conservative)
    remaining = merged["row_classification"] == "unclassified"
    merged.loc[remaining, "row_classification"] = "true_zero"

    step3_tz = (merged["row_classification"] == "true_zero").sum()
    step3_unc = (merged["row_classification"] == "uncertain").sum()
    step3_md_total = (merged["row_classification"] == "missing_data").sum()
    print(f"  Step 3: {step3_tz:,} true_zero, {step3_unc:,} uncertain, {step3_md_total:,} total missing_data")

    # ── Save outputs ──
    output_cols = [c for c in merged.columns if c not in ["sell_frequency", "was_observed"]]
    merged[output_cols].to_csv(os.path.join(processed_dir, "sales_classified.csv"), index=False)

    classification_counts = merged["row_classification"].value_counts().to_dict()
    report = {
        "total_rows": len(merged),
        "original_observed_rows": int(merged["was_observed"].sum()),
        "reconstructed_rows": int(reconstructed_count),
        "classification_counts": {
            "observed": int(classification_counts.get("observed", 0)),
            "true_zero": int(classification_counts.get("true_zero", 0)),
            "missing_data": int(classification_counts.get("missing_data", 0)),
            "stockout_gap": int(classification_counts.get("stockout_gap", 0)),
            "uncertain": int(classification_counts.get("uncertain", 0)),
        },
        "low_stock_skus_detected": zero_stock_skus,
        "unique_skus": int(merged["sku_id"].nunique()),
        "unique_outlets": int(merged["outlet_id"].nunique()),
        "unique_weeks": len(all_weeks),
        "date_range": {
            "start": str(merged["week_start_date"].min().date()),
            "end": str(merged["week_start_date"].max().date())
        }
    }

    with open(os.path.join(processed_dir, "classification_report.json"), "w") as f:
        json.dump(report, f, indent=2)

    # Generate methodology doc
    methodology = """# True Zero vs Missing Data Methodology

## Overview

When analyzing sales data from 320 outlets across Pune and Nashik, we face a critical question:
**When a row shows zero sales (or is missing entirely), does it mean the product genuinely had
no demand, or was the data simply not reported?**

Getting this wrong can catastrophically skew demand forecasts:
- Treating missing data as true zeros -> **underestimates demand** -> stockouts
- Treating true zeros as missing data -> **overestimates demand** -> overstock

## Step 0: Full Grid Reconstruction

The raw dataset only contains OBSERVED sales rows. However, the absence of a row
(outlet x SKU x week combination not present) is itself meaningful information.

We reconstruct the complete grid: every outlet x every SKU x every week.
Missing combinations are left-joined and marked for classification.

## Step 1: Outlet Weekly Reporting Check

If an outlet reports ZERO sales across ALL SKUs in a given week,
we conclude the outlet did not report that week at all.

Classification: All rows for that outlet x week -> missing_data

## Step 2: Stockout Gap Detection

If a SKU has very low warehouse stock (<=20 units) AND has periods where
sales dropped to zero, we classify those zero-sale periods as stockout_gap.

These zeros represent **unfulfilled demand**, not lack of demand.

## Step 3: Channel Frequency Baseline

Calculate how often each outlet actually sells each SKU:
sell_frequency = weeks_with_sales / total_reporting_weeks

Thresholds:
- sell_frequency > 0.6: Regular seller -> true_zero
- sell_frequency < 0.2: Rare seller -> missing_data
- 0.2 to 0.6: Ambiguous -> uncertain (treated as true_zero conservatively)

## How Classifications Are Used

| Classification | In Forecast? | Rationale |
|---|---|---|
| observed | Yes | Normal sales data |
| true_zero | Yes | Genuine zero demand |
| uncertain | Yes | Conservative: treat as real |
| stockout_gap | Yes* | *Included but flagged for the model |
| missing_data | No | Would artificially deflate demand |
"""

    with open(os.path.join(docs_dir, "true_zero_methodology.md"), "w", encoding="utf-8") as f:
        f.write(methodology)

    print(f"\n[1_clean_data] Complete!")
    print(f"  Total rows (full grid): {len(merged):,}")
    print(f"  Original observed: {int(merged['was_observed'].sum()):,}")
    print(f"  Reconstructed: {reconstructed_count:,}")
    print(f"  Classification: {classification_counts}")
    return True

if __name__ == "__main__":
    run()
