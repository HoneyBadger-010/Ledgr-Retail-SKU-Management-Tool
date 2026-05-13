"""
backend/4_reorder_engine.py -- Intelligent Reorder Calculator

Hard constraints: MOQ, shelf-life, safety stock, lead-time.
STRICT RULE: final_reorder_qty MUST NEVER exceed shelf_life_max.
Business impact: revenue_at_risk and overstock_value per SKU.

Bug fixes applied (Brief Part 3):
  - Bug 1: Week-by-week chronological simulation (no flattening anywhere)
  - Bug 2: Batch-aware available stock (excludes batches that expire before
           the next reorder can land); negative-availability clamped to 0
  - Bug 3: MAPE-driven dynamic safety stock replaces static 2-week buffer
  - Phase 12: EXPIRY_ALERT flag for SKUs whose earliest batch will expire
              before forecasted sell-through
"""
import pandas as pd, numpy as np, os, json
from datetime import datetime, timedelta
from math import ceil

def get_project_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_today(forecasts):
    """Anchor 'today' to the start of the first forecast week so stockout
    projections align with the data, not wall-clock time (Brief A7 fix)."""
    if "week_start_date" in forecasts.columns and len(forecasts) > 0:
        first_fc = pd.to_datetime(forecasts["week_start_date"]).min()
        return first_fc.to_pydatetime()
    return datetime.now()


def _connect_db():
    """Return a SQLAlchemy connection or None if DB is unavailable.
    The reorder engine runs standalone (no Flask context), so we use a
    raw engine pointed at the configured DATABASE_URL."""
    try:
        from sqlalchemy import create_engine
        root = get_project_root()
        db_uri = os.environ.get("DATABASE_URL",
                                f"sqlite:///{os.path.join(root, 'sunrise.db')}")
        if db_uri.startswith("postgres://"):
            db_uri = "postgresql://" + db_uri[len("postgres://"):]
        eng = create_engine(db_uri, future=True)
        return eng.connect()
    except Exception as e:
        print(f"  [warn] DB connect failed ({e}); falling back to warehouse_stock for all SKUs")
        return None


def _batch_summary(conn, sku_code, today, lt_days):
    """Return (usable_qty_after_lt, earliest_expiry_date) for a SKU.
    usable_qty: sum of qty_received across batches whose expiry > today + lt
    earliest_expiry: earliest expiry date across non-expired batches (any qty)
    Returns (None, None) if batches table empty or query fails."""
    try:
        from sqlalchemy import text
        cutoff = (today + timedelta(days=lt_days)).date().isoformat()
        today_iso = today.date().isoformat()
        rows = conn.execute(text("""
            SELECT b.qty_received, b.expiry_date
              FROM batches b
              JOIN skus s ON b.sku_id = s.id
             WHERE s.sku_code = :code
        """), {"code": sku_code}).fetchall()
        if not rows:
            return None, None
        usable = 0
        earliest = None
        for qty, exp in rows:
            if not exp:
                continue
            exp_str = str(exp)
            if exp_str > cutoff:
                usable += int(qty or 0)
            if exp_str >= today_iso:
                if earliest is None or exp_str < earliest:
                    earliest = exp_str
        return usable, earliest
    except Exception:
        return None, None


def _cumulative_forecast(week_forecasts, days):
    """Sum the chronological forecast over `days` days (full weeks plus a
    fractional partial week). Used in place of `daily_vel * days` so spike
    weeks aren't averaged into uniform demand (Brief A3 fix)."""
    if days <= 0 or not week_forecasts:
        return 0.0
    full_weeks = days // 7
    partial_days = days % 7
    n = len(week_forecasts)
    if full_weeks >= n:
        # asked for more days than we forecasted -> extrapolate at last-week rate
        total = float(sum(week_forecasts))
        extra_days = days - n * 7
        last_rate = week_forecasts[-1] / 7.0
        return total + last_rate * extra_days
    total = float(sum(week_forecasts[:full_weeks]))
    if partial_days:
        total += week_forecasts[full_weeks] * (partial_days / 7.0)
    return total


def run():
    root = get_project_root()
    data_dir = os.path.join(root, "data")
    processed_dir = os.path.join(root, "data", "processed")

    print("[4_reorder] Loading data...")
    forecasts = pd.read_csv(os.path.join(processed_dir, "forecasts.csv"))
    inventory = pd.read_csv(os.path.join(data_dir, "inventory_snapshot.csv"))
    sku_master = pd.read_csv(os.path.join(data_dir, "sku_master.csv"))

    festive = pd.read_csv(os.path.join(data_dir, "festive_calendar.csv"))
    festive["date"] = pd.to_datetime(festive["date"])

    accuracy_path = os.path.join(processed_dir, "forecast_accuracy.json")
    per_sku_mape = {}
    if os.path.exists(accuracy_path):
        with open(accuracy_path) as f:
            per_sku_mape = json.load(f).get("per_sku_mape", {})

    today = _resolve_today(forecasts)
    print(f"[4_reorder] Anchoring today = {today.date()} (first forecast week)")

    conn = _connect_db()
    batch_fallback_count = 0
    expiry_alerts = 0

    results = []
    shelf_life_violations = 0

    def is_festive_period():
        for i in range(4):
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

        week_forecasts = fc["forecasted_units"].tolist()
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

        # ── Bug 2 fix: batch-aware available stock ──
        # Prefer DB batches that survive past today+lead_time. Fall back to
        # warehouse_stock when no batch records exist for this SKU.
        usable_batches, earliest_expiry = (None, None)
        if conn is not None:
            usable_batches, earliest_expiry = _batch_summary(conn, sku, today, lt)
        if usable_batches is None:
            available_raw = wh + it - cq
            batch_fallback_count += 1
        else:
            available_raw = usable_batches + it - cq
        # Bug 2/A2: negative committed_qty must not poison the simulation
        available = max(0, available_raw)

        # ── Bug 1 fix: chronological week-by-week simulation ──
        available_sim = float(available)
        stockout_week = None
        for week_num in range(len(week_forecasts)):
            available_sim -= week_forecasts[week_num]
            if available_sim < 0:
                stockout_week = week_num + 1
                break

        if stockout_week is not None:
            days_of_stock = stockout_week * 7
            reorder_week = stockout_week - ceil(lt / 7)
        else:
            # No stockout in 6w: extrapolate at the last-week rate, never with the flattened mean
            last_week_rate = week_forecasts[-1] / 7.0 if week_forecasts and week_forecasts[-1] > 0 else 0.1
            days_of_stock = 42 + (available_sim / max(last_week_rate, 0.1))
            reorder_week = None

        weekly_forecast_avg = forecast_6w / 6.0 if forecast_6w > 0 else 0.0
        # daily_velocity is kept as a display field only (no longer drives logic)
        daily_vel = round(weekly_forecast_avg / 7.0, 2)
        weekly_vel = round(weekly_forecast_avg, 2)
        weeks_of_stock = round(days_of_stock / 7, 1)

        # ── Bug 1 fix continued: chronological shelf_life and lt_demand ──
        shelf_life_max = int(_cumulative_forecast(week_forecasts, sl))
        shelf_life_max = max(shelf_life_max, moq)
        lt_demand = int(_cumulative_forecast(week_forecasts, lt))

        # ── Bug 3 fix: MAPE-driven dynamic safety stock ──
        sku_mape_info = per_sku_mape.get(sku, {})
        sku_mape = sku_mape_info.get("mape", None)
        base_weeks = 1.0
        if sku_mape is None:
            volatility_multiplier = 1.5
        elif sku_mape < 10:
            volatility_multiplier = 0.5
        elif sku_mape <= 20:
            volatility_multiplier = 1.0
        else:
            volatility_multiplier = 2.0
        festive_multiplier = 1.5 if festive_active else 1.0
        safety_stock_weeks = base_weeks * volatility_multiplier * festive_multiplier
        safety_stock = int(weekly_forecast_avg * safety_stock_weeks)

        raw_qty = max(0, forecast_6w + safety_stock + lt_demand - available)

        is_dead = forecast_6w == 0
        is_overstock = weeks_of_stock > 18

        if is_dead or is_overstock:
            final_qty = 0
        elif raw_qty > 0 and raw_qty < moq:
            final_qty = moq
        elif raw_qty > 0:
            final_qty = int(np.ceil(raw_qty / moq) * moq)
        else:
            final_qty = 0

        if final_qty > shelf_life_max and not is_dead:
            final_qty = int(np.floor(shelf_life_max / moq) * moq) if moq > 0 else shelf_life_max
            if final_qty <= 0:
                final_qty = moq
            shelf_life_violations += 1

        assert final_qty <= shelf_life_max or is_dead, f"Shelf life violation for {sku}: {final_qty} > {shelf_life_max}"

        # ── Phase 12 / C3 fix: EXPIRY_ALERT for batches that won't sell through ──
        days_to_earliest_expiry = None
        expiry_alert_active = False
        if earliest_expiry:
            try:
                exp_dt = datetime.fromisoformat(earliest_expiry)
                days_to_earliest_expiry = (exp_dt.date() - today.date()).days
                # Forecasted weeks-to-sell-through: how many weeks of forecast
                # are needed to clear the current available stock?
                weeks_to_sell = 0
                acc = 0.0
                for wf in week_forecasts:
                    acc += wf
                    weeks_to_sell += 1
                    if acc >= available:
                        break
                if available > 0 and days_to_earliest_expiry < weeks_to_sell * 7:
                    expiry_alert_active = True
                    expiry_alerts += 1
            except Exception:
                pass

        # Flags
        flags = []
        if is_dead:
            flags.append("DEAD_STOCK")
        elif is_overstock:
            flags.append("OVERSTOCK_RISK")
        elif stockout_week is not None and reorder_week is not None and reorder_week <= 2:
            flags.append("STOCKOUT_RISK")
        elif stockout_week is not None and stockout_week <= ceil(lt / 7) + 2:
            flags.append("STOCKOUT_RISK")
        if expiry_alert_active:
            flags.append("EXPIRY_ALERT")
        if final_qty > 0 and final_qty >= shelf_life_max * 0.9:
            flags.append("SHELF_LIFE_CONSTRAINED")
        if sl < 90:
            flags.append("PERISHABLE")
        if not flags:
            flags.append("OK")

        revenue_at_risk = round(forecast_6w * up) if "STOCKOUT_RISK" in flags else 0
        excess_units = max(0, available - forecast_6w)
        overstock_value = round(excess_units * cp) if is_overstock else 0

        stockout_date_projected = None
        if stockout_week is not None:
            stockout_date_projected = (today + timedelta(weeks=stockout_week)).strftime("%Y-%m-%d")

        delivery_date = (today + timedelta(days=lt)).strftime("%Y-%m-%d")
        reason_parts = []
        if is_dead:
            reason_parts.append("DEAD STOCK: Zero sales forecast for next 6 weeks. No reorder recommended. Flag for clearance.")
        elif is_overstock:
            reason_parts.append(f"OVERSTOCK: Current stock of {available:.0f} units covers {weeks_of_stock:.1f} weeks (>18 week threshold). No reorder needed. Rs.{overstock_value:,.0f} capital trapped.")
        else:
            reason_parts.append(f"Recommending {final_qty} units.")
            if stockout_week:
                reason_parts.append(f"Week-by-week simulation shows stockout in week {stockout_week}.")
            else:
                reason_parts.append("No stockout projected within 6-week window.")
            reason_parts.append(f"Current usable stock: {available:.0f} units (wh={wh}, it={it}, cq={cq}{', batch-aware' if usable_batches is not None else ', warehouse fallback'}).")
            if "STOCKOUT_RISK" in flags:
                reason_parts.append(f"URGENT: Stock runs out before delivery (lead time: {lt} days). Revenue at risk: Rs.{revenue_at_risk:,.0f}.")
            if expiry_alert_active:
                reason_parts.append(f"EXPIRY_ALERT: Earliest batch expires in {days_to_earliest_expiry} days, before projected sell-through.")
            if "SHELF_LIFE_CONSTRAINED" in flags:
                reason_parts.append(f"Shelf-life constraint active: max {shelf_life_max} units saleable within {sl}-day window.")
            reason_parts.append(f"Safety stock: {safety_stock} units ({safety_stock_weeks:.1f}w, MAPE={sku_mape if sku_mape is not None else 'N/A'}%, festive={'Yes' if festive_active else 'No'}).")
            reason_parts.append(f"Lead-time demand: {lt_demand} units.")

        results.append({
            "sku_id": sku, "product_name": nm, "brand": br, "category": ca,
            "warehouse_stock": wh, "in_transit_qty": it, "committed_qty": cq,
            "available_stock": available, "forecast_6w_total": forecast_6w,
            "daily_velocity": daily_vel, "days_of_stock": round(days_of_stock, 1),
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
            "earliest_expiry_date": earliest_expiry or "",
            "days_to_earliest_expiry": days_to_earliest_expiry if days_to_earliest_expiry is not None else "",
            "mape_pct": sku_mape if sku_mape else 0,
            "flags": "|".join(flags),
            "revenue_at_risk": revenue_at_risk,
            "overstock_value": overstock_value,
            "reason_text": " ".join(reason_parts)
        })

    if conn is not None:
        conn.close()

    df = pd.DataFrame(results)
    df.to_csv(os.path.join(processed_dir, "reorder_recommendations.csv"), index=False)

    to_order = df[df["final_reorder_qty"] > 0]
    stockout = df[df["flags"].str.contains("STOCKOUT_RISK", na=False)]
    overstock = df[df["flags"].str.contains("OVERSTOCK_RISK", na=False)]
    dead = df[df["flags"].str.contains("DEAD_STOCK", na=False)]
    shelf_constrained = df[df["flags"].str.contains("SHELF_LIFE_CONSTRAINED", na=False)]
    expiry = df[df["flags"].str.contains("EXPIRY_ALERT", na=False)]

    actual_violations = df[(df["final_reorder_qty"] > 0) & (df["final_reorder_qty"] > df["shelf_life_max"])]
    assert len(actual_violations) == 0, f"CRITICAL: {len(actual_violations)} shelf life violations remain!"

    print(f"\n[4_reorder] Complete!")
    print(f"  Total SKUs: {len(df)}")
    print(f"  SKUs to reorder: {len(to_order)}")
    print(f"  Stockout risk: {len(stockout)}")
    print(f"  Overstock risk: {len(overstock)}")
    print(f"  Dead stock: {len(dead)}")
    print(f"  Shelf life constrained: {len(shelf_constrained)}")
    print(f"  Expiry alert: {len(expiry)}")
    print(f"  Batch-data fallback (warehouse only): {batch_fallback_count} SKUs")
    print(f"  Shelf life violations: 0 (MUST be 0)")
    print(f"  Total order value: Rs.{int(df['order_value_inr'].sum()):,}")
    print(f"  Total revenue at risk: Rs.{int(df['revenue_at_risk'].sum()):,}")
    print(f"  Total overstock capital: Rs.{int(df['overstock_value'].sum()):,}")
    return True

if __name__ == "__main__":
    run()
