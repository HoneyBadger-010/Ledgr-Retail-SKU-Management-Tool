"""
backend/4_reorder_engine.py -- Intelligent Reorder Calculator

Hard constraints: MOQ, shelf-life, safety stock, lead-time.
STRICT RULE: final_reorder_qty MUST NEVER exceed shelf_life_max.
Business impact: revenue_at_risk and overstock_value per SKU.
"""
import pandas as pd, numpy as np, os, json
from datetime import datetime, timedelta

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

    today = datetime.now()
    results = []
    shelf_life_violations = 0

    for sku in sorted(forecasts["sku_id"].unique()):
        fc = forecasts[forecasts["sku_id"] == sku]
        inv = inventory[inventory["sku_id"] == sku]
        ski = sku_master[sku_master["sku_id"] == sku]
        if len(inv) == 0 or len(ski) == 0:
            continue

        # Extract parameters
        forecast_6w = int(fc["forecasted_units"].sum())
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
        daily_vel = max(forecast_6w / 42, 0.1)
        weekly_vel = daily_vel * 7
        days_of_stock = available / daily_vel if daily_vel > 0 else 999
        weeks_of_stock = round(days_of_stock / 7, 1)

        # Shelf-life maximum: how many units can be sold before expiry
        shelf_life_max = int(daily_vel * sl)
        shelf_life_max = max(shelf_life_max, moq)

        # Safety stock: 2 weeks of demand
        safety_stock = int(weekly_vel * 2)

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

        # Flags
        flags = []
        if is_dead:
            flags.append("DEAD_STOCK")
        elif is_overstock:
            flags.append("OVERSTOCK_RISK")
        elif days_of_stock < lt:
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

        # Build reasoning
        delivery_date = (today + timedelta(days=lt)).strftime("%Y-%m-%d")
        reason_parts = []
        if is_dead:
            reason_parts.append(f"DEAD STOCK: Zero sales forecast for next 6 weeks. No reorder recommended. Flag for clearance.")
        elif is_overstock:
            reason_parts.append(f"OVERSTOCK: Current stock of {available:.0f} units covers {weeks_of_stock:.1f} weeks (>{3*6} week threshold). No reorder needed. Rs.{overstock_value:,.0f} capital trapped.")
        else:
            reason_parts.append(f"Recommending {final_qty} units.")
            reason_parts.append(f"Current stock of {available:.0f} units lasts ~{days_of_stock:.0f} days at {daily_vel:.1f} units/day.")
            if "STOCKOUT_RISK" in flags:
                reason_parts.append(f"URGENT: Stock runs out before delivery (lead time: {lt} days). Revenue at risk: Rs.{revenue_at_risk:,.0f}.")
            if "SHELF_LIFE_CONSTRAINED" in flags:
                reason_parts.append(f"Shelf-life constraint active: max {shelf_life_max} units can be sold within {sl}-day window.")
            reason_parts.append(f"Includes {safety_stock} units safety stock + {lt_demand} units lead-time demand.")

        results.append({
            "sku_id": sku, "product_name": nm, "brand": br, "category": ca,
            "warehouse_stock": wh, "in_transit_qty": it, "committed_qty": cq,
            "available_stock": available, "forecast_6w_total": forecast_6w,
            "daily_velocity": round(daily_vel, 2), "days_of_stock": round(days_of_stock, 1),
            "weeks_of_stock": weeks_of_stock, "safety_stock": safety_stock,
            "lt_demand": lt_demand, "raw_reorder_qty": raw_qty,
            "shelf_life_max": shelf_life_max, "final_reorder_qty": final_qty,
            "supplier_lead_time_days": lt, "moq": moq, "shelf_life_days": sl,
            "unit_price": up, "cost_price": cp,
            "order_value_inr": round(final_qty * cp),
            "delivery_date": delivery_date,
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
