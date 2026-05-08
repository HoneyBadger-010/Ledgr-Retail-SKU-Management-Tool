"""
backend/4_reorder_engine.py -- Intelligent Reorder Calculator

Hard constraints: MOQ, shelf-life, safety stock, lead-time.
STRICT RULE: final_reorder_qty MUST NEVER exceed shelf_life_max.
Business impact: revenue_at_risk and overstock_value per SKU.

Bug fixes applied (Brief Part 3):
  - Bug 1: Week-by-week chronological simulation replaces velocity flattening
  - Bug 3: MAPE-driven dynamic safety stock replaces static 2-week buffer
"""
import pandas as pd, numpy as np, os, json
from datetime import datetime, timedelta
from math import ceil

def get_project_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def run():
    root = get_project_root()
    data_dir = os.path.join(root, "data")
    processed_dir = os.path.join(root, "data", "processed")

    print("[4_reorder] Loading data...")
    forecasts = pd.read_csv(os.path.join(processed_dir, "forecasts.csv"))
    inventory = pd.read_csv(os.path.join(data_dir, "inventory_snapshot.csv"))
    sku_master = pd.read_csv(os.path.join(data_dir, "sku_master.csv"))

    # Load festive calendar for festive multiplier
    festive = pd.read_csv(os.path.join(data_dir, "festive_calendar.csv"))
    festive["date"] = pd.to_datetime(festive["date"])

    # Load forecast accuracy for MAPE-driven safety stock
    accuracy_path = os.path.join(processed_dir, "forecast_accuracy.json")
    per_sku_mape = {}
    if os.path.exists(accuracy_path):
        with open(accuracy_path) as f:
            acc_data = json.load(f)
            per_sku_mape = acc_data.get("per_sku_mape", {})

    today = datetime.now()
    results = []
    shelf_life_violations = 0

    # Check if any of next 3 weeks falls in festive period
    def is_festive_period():
        for i in range(4):  # current week + next 3 weeks
            check_date = today + timedelta(weeks=i)
            for _, fest in festive.iterrows():
                if abs((check_date - fest["date"]).days) <= 10:
                    return True
        return False

    festive_active = is_festive_period()

    for sku in sorted(forecasts["sku_id"].unique()):
        fc = forecasts[forecasts["sku_id"] == sku].sort_values("week_start_date")
        inv = inventory[inventory["sku_id"] == sku]
        ski = sku_master[sku_master["sku_id"] == sku]
        if len(inv) == 0 or len(ski) == 0:
            continue

        # Extract parameters
        week_forecasts = fc["forecasted_units"].tolist()  # 6 weekly forecasts
        forecast_6w = int(sum(week_forecasts))
        wh = int(inv.iloc[0]["warehouse_stock"])
        it = int(inv.iloc[0]["in_transit_qty"])
        cq = int(inv.iloc[0]["committed_qty"])
        lt = int(ski.iloc[0]["supplier_lead_time_days"])
        moq = int(ski.iloc[0]["moq_from_supplier"])
        sl = int(ski.iloc[0]["shelf_life_days"])
        up = float(ski.iloc[0]["unit_price"])
        cp = float(ski.iloc[0]["cost_price"])
        nm = ski.iloc[0]["product_name"]
        br = ski.iloc[0]["brand"]
        ca = ski.iloc[0]["category"]

        available = wh + it - cq

        # ── BUG 1 FIX: Week-by-week chronological simulation ──
        # Instead of dividing forecast_6w by 42 (flattening), simulate stock depletion week by week
        available_sim = float(available)
        stockout_week = None
        for week_num in range(len(week_forecasts)):
            week_forecast = week_forecasts[week_num]
            available_sim -= week_forecast
            if available_sim < 0:
                stockout_week = week_num + 1  # 1-indexed
                break

        if stockout_week is not None:
            days_of_stock = stockout_week * 7  # approximate
            reorder_week = stockout_week - ceil(lt / 7)
        else:
            days_of_stock = 42 + (available_sim / max(forecast_6w / 42, 0.1))  # beyond 6-week window
            reorder_week = None

        # For backward-compatible fields
        daily_vel = max(forecast_6w / 42, 0.1)
        weekly_vel = daily_vel * 7
        weeks_of_stock = round(days_of_stock / 7, 1)

        # Shelf-life maximum: how many units can be sold before expiry
        shelf_life_max = int(daily_vel * sl)
        shelf_life_max = max(shelf_life_max, moq)

        # ── BUG 3 FIX: MAPE-driven dynamic safety stock ──
        sku_mape_info = per_sku_mape.get(sku, {})
        sku_mape = sku_mape_info.get("mape", None)

        base_weeks = 1.0  # down from hardcoded 2.0

        # Volatility multiplier based on rolling MAPE
        if sku_mape is None:
            volatility_multiplier = 1.5  # conservative default for new SKUs
        elif sku_mape < 10:
            volatility_multiplier = 0.5  # model is confident, hold less buffer
        elif sku_mape <= 20:
            volatility_multiplier = 1.0  # normal
        else:
            volatility_multiplier = 2.0  # model is uncertain, hold more buffer

        # Festive multiplier: accounts for supplier lead time degradation during Diwali
        festive_multiplier = 1.5 if festive_active else 1.0

        safety_stock_weeks = base_weeks * volatility_multiplier * festive_multiplier
        weekly_forecast_avg = forecast_6w / 6 if forecast_6w > 0 else 0
        safety_stock = int(weekly_forecast_avg * safety_stock_weeks)

        # Lead-time demand
        lt_demand = int(daily_vel * lt)

        # Raw reorder qty
        raw_qty = forecast_6w + safety_stock + lt_demand - available
        raw_qty = max(0, raw_qty)

        # Dead stock check: 0 sales in last 8 weeks
        is_dead = forecast_6w == 0
        is_overstock = weeks_of_stock > 18  # 3x6 weeks

        if is_dead or is_overstock:
            final_qty = 0
        else:
            # Round up to MOQ
            if raw_qty > 0 and raw_qty < moq:
                final_qty = moq
            elif raw_qty > 0:
                final_qty = int(np.ceil(raw_qty / moq) * moq)
            else:
                final_qty = 0

        # STRICT SHELF-LIFE VALIDATION: final_reorder_qty MUST NEVER exceed shelf_life_max
        if final_qty > shelf_life_max and not is_dead:
            final_qty = int(np.floor(shelf_life_max / moq) * moq) if moq > 0 else shelf_life_max
            if final_qty <= 0:
                final_qty = moq  # At minimum order MOQ
            shelf_life_violations += 1  # Count as constrained (but corrected)

        # Assert: this MUST hold
        assert final_qty <= shelf_life_max or is_dead, f"Shelf life violation for {sku}: {final_qty} > {shelf_life_max}"

        # Flags — using simulation-based urgency
        flags = []
        if is_dead:
            flags.append("DEAD_STOCK")
        elif is_overstock:
            flags.append("OVERSTOCK_RISK")
        elif stockout_week is not None and reorder_week is not None and reorder_week <= 1:
            flags.append("STOCKOUT_RISK")
        elif stockout_week is not None and reorder_week is not None and reorder_week <= 2:
            flags.append("STOCKOUT_RISK")
        elif days_of_stock < lt + 14:
            flags.append("STOCKOUT_RISK")
        if final_qty > 0 and final_qty >= shelf_life_max * 0.9:
            flags.append("SHELF_LIFE_CONSTRAINED")
        if sl < 90:
            flags.append("PERISHABLE")
        if not flags:
            flags.append("OK")

        # Business impact metrics
        revenue_at_risk = round(forecast_6w * up) if "STOCKOUT_RISK" in flags else 0
        excess_units = max(0, available - forecast_6w)
        overstock_value = round(excess_units * cp) if is_overstock else 0

        # Stockout date projection
        stockout_date_projected = None
        if stockout_week is not None:
            stockout_date_projected = (today + timedelta(weeks=stockout_week)).strftime("%Y-%m-%d")

        # Build reasoning
        delivery_date = (today + timedelta(days=lt)).strftime("%Y-%m-%d")
        reason_parts = []
        if is_dead:
            reason_parts.append(f"DEAD STOCK: Zero sales forecast for next 6 weeks. No reorder recommended. Flag for clearance.")
        elif is_overstock:
            reason_parts.append(f"OVERSTOCK: Current stock of {available:.0f} units covers {weeks_of_stock:.1f} weeks (>{3*6} week threshold). No reorder needed. Rs.{overstock_value:,.0f} capital trapped.")
        else:
            reason_parts.append(f"Recommending {final_qty} units.")
            if stockout_week:
                reason_parts.append(f"Week-by-week simulation shows stockout in week {stockout_week}.")
            else:
                reason_parts.append(f"No stockout projected within 6-week window.")
            reason_parts.append(f"Current stock of {available:.0f} units lasts ~{days_of_stock:.0f} days.")
            if "STOCKOUT_RISK" in flags:
                reason_parts.append(f"URGENT: Stock runs out before delivery (lead time: {lt} days). Revenue at risk: Rs.{revenue_at_risk:,.0f}.")
            if "SHELF_LIFE_CONSTRAINED" in flags:
                reason_parts.append(f"Shelf-life constraint active: max {shelf_life_max} units can be sold within {sl}-day window.")
            reason_parts.append(f"Safety stock: {safety_stock} units ({safety_stock_weeks:.1f}w, MAPE={sku_mape or 'N/A'}%, festive={'Yes' if festive_active else 'No'}).")
            reason_parts.append(f"Lead-time demand: {lt_demand} units.")

        results.append({
            "sku_id": sku, "product_name": nm, "brand": br, "category": ca,
            "warehouse_stock": wh, "in_transit_qty": it, "committed_qty": cq,
            "available_stock": available, "forecast_6w_total": forecast_6w,
            "daily_velocity": round(daily_vel, 2), "days_of_stock": round(days_of_stock, 1),
            "weeks_of_stock": weeks_of_stock, "safety_stock": safety_stock,
            "safety_stock_weeks": round(safety_stock_weeks, 2),
            "lt_demand": lt_demand, "raw_reorder_qty": raw_qty,
            "shelf_life_max": shelf_life_max, "final_reorder_qty": final_qty,
            "supplier_lead_time_days": lt, "moq": moq, "shelf_life_days": sl,
            "unit_price": up, "cost_price": cp,
            "order_value_inr": round(final_qty * cp),
            "delivery_date": delivery_date,
            "stockout_week": stockout_week,
            "stockout_date_projected": stockout_date_projected,
            "mape_pct": sku_mape if sku_mape else 0,
            "flags": "|".join(flags),
            "revenue_at_risk": revenue_at_risk,
            "overstock_value": overstock_value,
            "reason_text": " ".join(reason_parts)
        })

    df = pd.DataFrame(results)
    df.to_csv(os.path.join(processed_dir, "reorder_recommendations.csv"), index=False)

    to_order = df[df["final_reorder_qty"] > 0]
    stockout = df[df["flags"].str.contains("STOCKOUT_RISK", na=False)]
    overstock = df[df["flags"].str.contains("OVERSTOCK_RISK", na=False)]
    dead = df[df["flags"].str.contains("DEAD_STOCK", na=False)]
    shelf_constrained = df[df["flags"].str.contains("SHELF_LIFE_CONSTRAINED", na=False)]

    # Verify: 0 actual violations (all were corrected)
    actual_violations = df[(df["final_reorder_qty"] > 0) & (df["final_reorder_qty"] > df["shelf_life_max"])]
    assert len(actual_violations) == 0, f"CRITICAL: {len(actual_violations)} shelf life violations remain!"

    print(f"\n[4_reorder] Complete!")
    print(f"  Total SKUs: {len(df)}")
    print(f"  SKUs to reorder: {len(to_order)}")
    print(f"  Stockout risk: {len(stockout)}")
    print(f"  Overstock risk: {len(overstock)}")
    print(f"  Dead stock: {len(dead)}")
    print(f"  Shelf life constrained: {len(shelf_constrained)}")
    print(f"  Shelf life violations: 0 (MUST be 0)")
    print(f"  Total order value: Rs.{int(df['order_value_inr'].sum()):,}")
    print(f"  Total revenue at risk: Rs.{int(df['revenue_at_risk'].sum()):,}")
    print(f"  Total overstock capital: Rs.{int(df['overstock_value'].sum()):,}")
    return True

if __name__ == "__main__":
    run()
