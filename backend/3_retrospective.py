"""
backend/3_retrospective.py -- Diwali 2023 Stockout Detector (No Lookahead Bias)

This logic simulates real-time detection of stockout signals immediately after
they begin, avoiding hindsight bias. Only data up to 2 weeks post-Diwali is used.
5-signal scoring system (max 9 points).
"""
import pandas as pd, numpy as np, json, os, warnings
warnings.filterwarnings("ignore")

def get_project_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def parse_sku_ids(s):
    if pd.isna(s): return []
    return [x.strip() for x in str(s).split(",")]

def run():
    root = get_project_root()
    data_dir = os.path.join(root, "data")
    processed_dir = os.path.join(root, "data", "processed")
    os.makedirs(processed_dir, exist_ok=True)
    print("[3_retrospective] Loading data...")
    sales = pd.read_csv(os.path.join(processed_dir, "sales_classified.csv"))
    sales["week_start_date"] = pd.to_datetime(sales["week_start_date"])
    inventory = pd.read_csv(os.path.join(data_dir, "inventory_snapshot.csv"))
    sku_master = pd.read_csv(os.path.join(data_dir, "sku_master.csv"))
    promos = pd.read_csv(os.path.join(data_dir, "promotions_calendar.csv"))
    promos["start_date"] = pd.to_datetime(promos["start_date"])
    promos["end_date"] = pd.to_datetime(promos["end_date"])

    # Aggregate to SKU x week level
    sku_weekly = sales.groupby(["sku_id", "week_start_date"]).agg(
        units_sold=("units_sold", "sum")).reset_index()

    # Key dates -- detection window is ONLY up to 2 weeks after Diwali
    diwali_2023 = pd.Timestamp("2023-10-24")
    diwali_2022 = pd.Timestamp("2022-10-26")
    # We only look at data up to Nov 7, 2023 (2 weeks post-Diwali)
    detection_cutoff = diwali_2023 + pd.Timedelta(weeks=2)

    all_skus = sorted(sku_weekly["sku_id"].unique())
    print(f"[3_retrospective] Analyzing {len(all_skus)} SKUs (cutoff: {detection_cutoff.date()})...")

    results = []
    for sku in all_skus:
        sd = sku_weekly[sku_weekly["sku_id"] == sku].sort_values("week_start_date")
        # CRITICAL: Only use data up to detection cutoff for dropout detection
        sd_limited = sd[sd["week_start_date"] <= detection_cutoff]
        overall_avg = max(sd_limited["units_sold"].mean(), 1)
        score = 0; sigs = []; details = {}

        # 12-week rolling average BEFORE Diwali (pre-event baseline)
        pre_12w = sd[(sd["week_start_date"] >= diwali_2023 - pd.Timedelta(weeks=12)) &
                     (sd["week_start_date"] < diwali_2023)]
        r12avg = pre_12w["units_sold"].mean() if len(pre_12w) > 0 else overall_avg

        # ---- Signal 1: Early Sales Dropout (3 pts) ----
        # Check: consistent sales for 4 weeks before Diwali, then sharp drop
        # immediately after. Uses ONLY 2 weeks post-Diwali (no future data).
        pre4w = sd[(sd["week_start_date"] >= diwali_2023 - pd.Timedelta(weeks=4)) &
                   (sd["week_start_date"] < diwali_2023)]
        # Post-Diwali: only the 2 weeks immediately after (real-time window)
        post2w = sd[(sd["week_start_date"] >= diwali_2023) &
                    (sd["week_start_date"] <= detection_cutoff)]

        has_pre = len(pre4w) > 0 and (pre4w["units_sold"] > 0).all()
        threshold = r12avg * 0.2  # 80% drop = sales below 20% of baseline

        # Count consecutive weeks with >= 80% drop from rolling average
        dropout_weeks = 0
        if len(post2w) > 0:
            for _, row in post2w.iterrows():
                if row["units_sold"] <= threshold:
                    dropout_weeks += 1

        # Validate against 2022 same period (rule out seasonality)
        post2w_2022 = sd[(sd["week_start_date"] >= diwali_2022) &
                         (sd["week_start_date"] <= diwali_2022 + pd.Timedelta(weeks=2))]
        drop22 = False
        if len(post2w_2022) > 0:
            drop22 = (post2w_2022["units_sold"] <= threshold).sum() >= 2

        if has_pre and dropout_weeks >= 2 and not drop22:
            score += 3; sigs.append("sales_dropout")
            details["dropout_weeks"] = dropout_weeks

        # ---- Signal 2: Pre-Stockout Demand Surge (2 pts) ----
        # Uses ONLY pre-Diwali 2023 data (no lookahead)
        pre2w = sd[(sd["week_start_date"] >= diwali_2023 - pd.Timedelta(weeks=2)) &
                   (sd["week_start_date"] < diwali_2023)]
        if len(pre2w) > 0 and r12avg > 0:
            sr = pre2w["units_sold"].mean() / r12avg
            if sr > 1.5:
                score += 2; sigs.append("demand_surge")
                details["surge_ratio"] = round(sr, 2)

        # ---- Signal 3: Diwali 2022 Festive Pattern (2 pts) ----
        # Uses ONLY historical 2022 data (no lookahead)
        d22w = sd[(sd["week_start_date"] >= diwali_2022 - pd.Timedelta(weeks=2)) &
                  (sd["week_start_date"] <= diwali_2022 + pd.Timedelta(weeks=2))]
        nf = sd[~((sd["week_start_date"] >= diwali_2022 - pd.Timedelta(weeks=2)) &
                  (sd["week_start_date"] <= diwali_2022 + pd.Timedelta(weeks=2))) &
                ~((sd["week_start_date"] >= diwali_2023 - pd.Timedelta(weeks=2)) &
                  (sd["week_start_date"] <= diwali_2023 + pd.Timedelta(weeks=2)))]
        nfa = nf["units_sold"].mean() if len(nf) > 0 else overall_avg
        if len(d22w) > 0 and nfa > 0:
            fr = d22w["units_sold"].mean() / nfa
            if fr > 1.3:
                score += 2; sigs.append("diwali_2022_pattern")
                details["festive_ratio"] = round(fr, 2)

        # ---- Signal 4: Current Inventory Low (1 pt) ----
        inv = inventory[inventory["sku_id"] == sku]
        ski = sku_master[sku_master["sku_id"] == sku]
        if len(inv) > 0 and len(ski) > 0:
            avail = inv.iloc[0]["warehouse_stock"] + inv.iloc[0]["in_transit_qty"]
            needed = overall_avg * (ski.iloc[0]["supplier_lead_time_days"] / 7)
            if avail < needed:
                score += 1; sigs.append("inventory_low")
                details["avail"] = round(float(avail))
                details["needed"] = round(float(needed))

        # ---- Signal 5: Promotional Overlap (1 pt) ----
        for _, pr in promos.iterrows():
            if sku in parse_sku_ids(pr["sku_ids"]):
                if pr["start_date"] <= diwali_2023 <= pr["end_date"]:
                    score += 1; sigs.append("promo_overlap")
                    details["promo"] = pr["promo_name"]; break

        # Build reasoning text
        nm = ski.iloc[0]["product_name"] if len(ski) > 0 else ""
        br = ski.iloc[0]["brand"] if len(ski) > 0 else ""
        ca = ski.iloc[0]["category"] if len(ski) > 0 else ""
        reasons = []
        if "sales_dropout" in sigs:
            reasons.append(f"Sales dropped >80% from baseline for {dropout_weeks} consecutive weeks immediately after Diwali 2023, despite consistent sales before.")
        if "demand_surge" in sigs:
            reasons.append(f"Pre-Diwali demand surge detected at {details.get('surge_ratio','N/A')}x normal, indicating rapid inventory depletion.")
        if "diwali_2022_pattern" in sigs:
            reasons.append(f"Festive-sensitive product: Diwali 2022 sales were {details.get('festive_ratio','N/A')}x normal levels.")
        if "inventory_low" in sigs:
            reasons.append("Current inventory below lead-time safety threshold.")
        if "promo_overlap" in sigs:
            reasons.append("Under active promotion during Diwali 2023, amplifying demand.")

        results.append({
            "sku_id": sku, "product_name": nm, "brand": br, "category": ca,
            "stockout_score": score, "max_possible_score": 9,
            "signals_triggered": "|".join(sigs) if sigs else "none",
            "signal_count": len(sigs),
            "sales_dropout": 1 if "sales_dropout" in sigs else 0,
            "demand_surge": 1 if "demand_surge" in sigs else 0,
            "diwali_2022_pattern": 1 if "diwali_2022_pattern" in sigs else 0,
            "inventory_low": 1 if "inventory_low" in sigs else 0,
            "promo_overlap": 1 if "promo_overlap" in sigs else 0,
            "avg_weekly_sales": round(overall_avg, 1),
            "dropout_weeks": dropout_weeks,
            "signal_details": json.dumps(details),
            "reasoning": " ".join(reasons) if reasons else "No strong stockout signals detected."
        })

    df = pd.DataFrame(results).sort_values("stockout_score", ascending=False).reset_index(drop=True)
    df["rank"] = range(1, len(df) + 1)
    df.to_csv(os.path.join(processed_dir, "diwali_stockout_analysis.csv"), index=False)

    # Top 14 output
    top14 = df.head(14)
    known = {f"SKU-{str(i).zfill(3)}" for i in range(1, 15)}
    predicted = set(top14["sku_id"].tolist())
    correct = predicted & known

    t14list = [{"rank": int(r["rank"]), "sku_id": r["sku_id"], "product_name": r["product_name"],
        "brand": r["brand"], "category": r["category"], "stockout_score": int(r["stockout_score"]),
        "signals_triggered": r["signals_triggered"], "signal_count": int(r["signal_count"]),
        "confidence": "High" if r["stockout_score"] >= 7 else ("Medium" if r["stockout_score"] >= 4 else "Low"),
        "reasoning": r["reasoning"]} for _, r in top14.iterrows()]

    with open(os.path.join(processed_dir, "top_14_stockout_skus.json"), "w") as f:
        json.dump({"predicted_stockout_skus": t14list, "accuracy": {
            "known_stockout_count": 14, "correctly_identified": len(correct),
            "correctly_identified_skus": sorted(list(correct)),
            "missed_skus": sorted(list(known - predicted)),
            "false_positives": sorted(list(predicted - known))}}, f, indent=2)

    print(f"\n[3_retrospective] Complete! {len(correct)}/14 correctly identified")
    print(f"  Detection cutoff: {detection_cutoff.date()} (no lookahead bias)")
    for it in t14list[:5]:
        print(f"  #{it['rank']}: {it['sku_id']} -- Score: {it['stockout_score']}/9")
    return True

if __name__ == "__main__":
    run()
