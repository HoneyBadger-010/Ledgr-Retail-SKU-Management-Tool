"""
app.py — Flask web application with Tabler UI dashboard
Serves API endpoints and HTML pages for the demand forecasting system.
Integrated with Flask-Login auth (Brief Part 2B).
"""
import os, sys, json, threading
import pandas as pd
import requests as http_requests
from flask import Flask, render_template, jsonify, request, send_file
from flask_login import login_required, current_user

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "sunrise-dev-key-change-in-prod")

# Auth integration (Brief Part 2B)
from auth import auth_bp, init_auth
app.register_blueprint(auth_bp)
init_auth(app)

ROOT = os.path.dirname(os.path.abspath(__file__))
PROCESSED = os.path.join(ROOT, "data", "processed")
DATA = os.path.join(ROOT, "data")

def ensure_pipeline():
    """Run pipeline if processed data doesn't exist."""
    report_path = os.path.join(PROCESSED, "monday_report.json")
    if not os.path.exists(report_path):
        sys.path.insert(0, ROOT)
        from pipeline import run_pipeline
        run_pipeline()

def load_json(name):
    path = os.path.join(PROCESSED, name)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}

def load_csv(name, directory=None):
    path = os.path.join(directory or PROCESSED, name)
    if os.path.exists(path):
        return pd.read_csv(path)
    return pd.DataFrame()

# ── Pages (all protected by login_required) ──
@app.route("/")
@login_required
def index():
    return render_template("overview.html", page="overview")

@app.route("/retrospective")
@login_required
def retrospective():
    return render_template("retrospective.html", page="retrospective")

@app.route("/forecast")
@login_required
def forecast():
    return render_template("forecast.html", page="forecast")

@app.route("/reorder")
@login_required
def reorder():
    return render_template("reorder.html", page="reorder")

@app.route("/classification")
@login_required
def classification():
    return render_template("classification.html", page="classification")

@app.route("/accuracy")
@login_required
def accuracy():
    return render_template("accuracy.html", page="accuracy")

@app.route("/outlets")
@login_required
def outlets():
    return render_template("outlets.html", page="outlets")

@app.route("/sku-management")
@login_required
def sku_management():
    return render_template("sku_management.html", page="sku_management")

@app.route("/data-quality")
@login_required
def data_quality():
    return render_template("data_quality.html", page="data_quality")

@app.route("/supplier-performance")
@login_required
def supplier_performance():
    return render_template("supplier_performance.html", page="supplier")

# ── API Endpoints ──
@app.route("/api/report")
def api_report():
    return jsonify(load_json("monday_report.json"))

@app.route("/api/stockout-analysis")
def api_stockout():
    df = load_csv("diwali_stockout_analysis.csv")
    return jsonify(df.head(40).to_dict(orient="records"))

@app.route("/api/top14")
def api_top14():
    return jsonify(load_json("top_14_stockout_skus.json"))

@app.route("/api/forecasts")
def api_forecasts():
    df = load_csv("forecasts.csv")
    sku = request.args.get("sku")
    if sku:
        df = df[df["sku_id"] == sku]
    return jsonify(df.to_dict(orient="records"))

@app.route("/api/forecast-accuracy")
def api_forecast_accuracy():
    return jsonify(load_json("forecast_accuracy.json"))

@app.route("/api/reorder-recommendations")
def api_reorder_recs():
    df = load_csv("reorder_recommendations.csv")
    flag = request.args.get("flag")
    if flag and flag != "All":
        df = df[df["flags"].str.contains(flag, na=False)]
    return jsonify(df.to_dict(orient="records"))

@app.route("/api/sku-classification")
def api_sku_class():
    df = load_csv("sku_classification.csv")
    return jsonify(df.to_dict(orient="records"))

@app.route("/api/sku-list")
def api_sku_list():
    df = load_csv("sku_master.csv", DATA)
    return jsonify(df[["sku_id", "product_name", "brand", "category"]].to_dict(orient="records"))

@app.route("/api/sku-sales/<sku_id>")
def api_sku_sales(sku_id):
    sales = load_csv("sales_classified.csv")
    if len(sales) == 0:
        return jsonify([])
    sales["week_start_date"] = pd.to_datetime(sales["week_start_date"])
    sku_data = sales[sales["sku_id"] == sku_id].groupby("week_start_date").agg(
        units_sold=("units_sold", "sum")).reset_index()
    sku_data = sku_data.sort_values("week_start_date")
    sku_data["week_start_date"] = sku_data["week_start_date"].dt.strftime("%Y-%m-%d")
    return jsonify(sku_data.to_dict(orient="records"))

@app.route("/api/classification-report")
def api_class_report():
    return jsonify(load_json("classification_report.json"))

pipeline_status = {"running": False, "status": "idle", "error": None}

@app.route("/api/run-pipeline", methods=["POST"])
def api_run_pipeline():
    """Run pipeline asynchronously (Brief Phase 7 — async fix)."""
    if pipeline_status["running"]:
        return jsonify({"status": "already_running", "message": "Pipeline is already running"})

    def run_async():
        pipeline_status["running"] = True
        pipeline_status["status"] = "running"
        pipeline_status["error"] = None
        try:
            sys.path.insert(0, ROOT)
            from pipeline import run_pipeline
            results = run_pipeline()
            pipeline_status["status"] = "success"
        except Exception as e:
            pipeline_status["status"] = "error"
            pipeline_status["error"] = str(e)
        finally:
            pipeline_status["running"] = False

    t = threading.Thread(target=run_async, daemon=True)
    t.start()
    return jsonify({"status": "started", "message": "Pipeline started in background"})

@app.route("/api/pipeline-status")
def api_pipeline_status():
    return jsonify(pipeline_status)

@app.route("/api/download-reorder")
def download_reorder():
    path = os.path.join(PROCESSED, "reorder_recommendations.csv")
    if os.path.exists(path):
        return send_file(path, as_attachment=True, download_name="reorder_plan.csv")
    return "File not found", 404

# ── SKU Management API (Brief Part 2F) ──
@app.route("/api/sku-list-full")
def api_sku_list_full():
    """Full SKU master data for the management table."""
    df = load_csv("sku_master.csv", DATA)
    return jsonify(df.to_dict(orient="records"))

@app.route("/api/sku/create", methods=["POST"])
def api_sku_create():
    """Add a new SKU to the master file."""
    try:
        data = request.json
        df = load_csv("sku_master.csv", DATA)
        sku_id = data.get("sku_code", "").strip()
        if not sku_id:
            return jsonify({"status": "error", "message": "SKU code is required"})
        if sku_id in df["sku_id"].values:
            return jsonify({"status": "error", "message": f"{sku_id} already exists"})
        new_row = {
            "sku_id": sku_id,
            "product_name": data.get("product_name", ""),
            "brand": data.get("brand", ""),
            "category": data.get("category", ""),
            "subcategory": data.get("subcategory", ""),
            "unit_price": float(data.get("unit_price", 0)),
            "cost_price": float(data.get("cost_price", 0)),
            "shelf_life_days": int(data.get("shelf_life_days", 365)),
            "moq_from_supplier": int(data.get("moq_from_supplier", 6)),
            "supplier_lead_time_days": int(data.get("supplier_lead_time_days", 7)),
        }
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        df.to_csv(os.path.join(DATA, "sku_master.csv"), index=False)
        return jsonify({"status": "success", "message": f"SKU {sku_id} added successfully"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route("/api/sku/delete", methods=["POST"])
def api_sku_delete():
    """Delete a SKU from the master file."""
    try:
        data = request.json
        sku_id = data.get("sku_id", "").strip()
        df = load_csv("sku_master.csv", DATA)
        if sku_id not in df["sku_id"].values:
            return jsonify({"status": "error", "message": f"{sku_id} not found"})
        df = df[df["sku_id"] != sku_id]
        df.to_csv(os.path.join(DATA, "sku_master.csv"), index=False)
        return jsonify({"status": "success", "message": f"SKU {sku_id} deleted"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route("/api/sku/upload", methods=["POST"])
def api_sku_upload():
    """Upload CSV to add/update SKUs."""
    try:
        file = request.files.get("file")
        if not file:
            return jsonify({"status": "error", "message": "No file provided"})
        upload_df = pd.read_csv(file)
        required = ["sku_id", "product_name", "brand", "category"]
        # Try alternate column names
        col_map = {"sku_code": "sku_id"}
        upload_df.rename(columns=col_map, inplace=True)
        missing = [c for c in required if c not in upload_df.columns]
        if missing:
            return jsonify({"status": "error", "message": f"Missing columns: {', '.join(missing)}"})
        existing = load_csv("sku_master.csv", DATA)
        merged = pd.concat([existing, upload_df], ignore_index=True)
        merged = merged.drop_duplicates(subset=["sku_id"], keep="last")
        merged.to_csv(os.path.join(DATA, "sku_master.csv"), index=False)
        return jsonify({"status": "success", "message": f"Uploaded {len(upload_df)} SKUs ({len(merged)} total)"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

# ── Data Quality API (Brief Part 5A) ──
@app.route("/api/data-quality")
def api_data_quality():
    """Data quality metrics from the classified sales data."""
    try:
        sales = load_csv("sales_classified.csv")
        if len(sales) == 0:
            return jsonify({"accepted_rows": 0, "rejected_rows": 0, "acceptance_rate": 0})

        # Classification breakdown
        cls_counts = sales["row_classification"].value_counts().to_dict()
        total_rows = len(sales)
        accepted_classes = ["observed", "true_zero"]
        rejected_classes = ["missing_data", "stockout_gap", "uncertain_excluded"]
        accepted = sum(cls_counts.get(c, 0) for c in accepted_classes)
        rejected = sum(cls_counts.get(c, 0) for c in rejected_classes)
        rate = (accepted / total_rows * 100) if total_rows > 0 else 0

        # Weekly stats for chart (simulate from sales data by week)
        sales["week_start_date"] = pd.to_datetime(sales["week_start_date"])
        weeks = sorted(sales["week_start_date"].unique())
        last_8_weeks = weeks[-8:] if len(weeks) >= 8 else weeks

        weekly_accepted = []
        weekly_rejected = []
        weekly_labels = []
        for w in last_8_weeks:
            wk_data = sales[sales["week_start_date"] == w]
            wa = len(wk_data[wk_data["row_classification"].isin(accepted_classes)])
            wr = len(wk_data[wk_data["row_classification"].isin(rejected_classes)])
            weekly_accepted.append(wa)
            weekly_rejected.append(wr)
            weekly_labels.append(pd.Timestamp(w).strftime("%b %d"))

        # Rolling average for drift detection
        rolling_avg = []
        for i in range(len(weekly_accepted)):
            start = max(0, i - 3)
            avg = sum(weekly_accepted[start:i+1]) / len(weekly_accepted[start:i+1])
            rolling_avg.append(int(avg))

        # Check drift
        drift = False
        drift_pct = 0
        if len(weekly_accepted) >= 2 and len(rolling_avg) >= 2:
            last_val = weekly_accepted[-1]
            avg_val = rolling_avg[-2] if rolling_avg[-2] > 0 else 1
            drift_pct = round((avg_val - last_val) / avg_val * 100, 1)
            drift = drift_pct > 15

        # Outlets not reporting
        outlet_master = load_csv("outlet_master.csv", DATA)
        outlets_in_sales = sales[sales["week_start_date"] == weeks[-1]]["outlet_id"].unique() if len(weeks) > 0 else []
        all_outlets = outlet_master["outlet_id"].unique() if len(outlet_master) > 0 else []
        outlets_not_reporting = len(set(all_outlets) - set(outlets_in_sales))

        # Rejection reasons
        rejection_reasons = []
        for cls_name in rejected_classes:
            cnt = cls_counts.get(cls_name, 0)
            if cnt > 0:
                rejection_reasons.append({
                    "code": cls_name.upper(),
                    "description": {
                        "missing_data": "Outlet did not report sales for this SKU-week",
                        "stockout_gap": "Warehouse stock below MOQ threshold — likely stockout",
                        "uncertain_excluded": "Uncertain demand band excluded from training (channel-aware rule)"
                    }.get(cls_name, cls_name),
                    "count": cnt,
                    "pct": round(cnt / max(rejected, 1) * 100, 1)
                })

        # Outlet reporting data (buckets)
        outlet_reporting = sales.groupby("outlet_id")["week_start_date"].nunique()
        max_weeks = len(weeks)
        active = len(outlet_reporting[outlet_reporting >= max_weeks * 0.9])
        partial = len(outlet_reporting[(outlet_reporting >= max_weeks * 0.5) & (outlet_reporting < max_weeks * 0.9)])
        low = len(outlet_reporting[(outlet_reporting >= max_weeks * 0.1) & (outlet_reporting < max_weeks * 0.5)])
        missing_outlets = len(all_outlets) - len(outlet_reporting)

        return jsonify({
            "accepted_rows": accepted,
            "rejected_rows": rejected,
            "acceptance_rate": round(rate, 1),
            "outlets_not_reporting": outlets_not_reporting,
            "outlet_reporting_pct": round(len(outlets_in_sales) / max(len(all_outlets), 1) * 100, 1),
            "row_count_drift": drift,
            "drift_pct": drift_pct,
            "weekly_accepted": weekly_accepted,
            "weekly_rejected": weekly_rejected,
            "weekly_labels": weekly_labels,
            "rolling_avg": rolling_avg,
            "classification_labels": list(cls_counts.keys()),
            "classification_values": list(cls_counts.values()),
            "rejection_reasons": rejection_reasons,
            "outlet_reporting_data": [active, partial, low, max(missing_outlets, 0)]
        })
    except Exception as e:
        return jsonify({"error": str(e), "accepted_rows": 0, "rejected_rows": 0, "acceptance_rate": 0})

# ── Supplier Performance API (Brief Part 5C) ──
@app.route("/api/supplier-performance")
def api_supplier_performance():
    """Supplier lead time analytics from SKU master data."""
    try:
        sku = load_csv("sku_master.csv", DATA)
        if len(sku) == 0:
            return jsonify({"suppliers": [], "avg_lead_time": 0})

        import numpy as np
        avg_lt = sku["supplier_lead_time_days"].mean()
        p80_lt = float(np.percentile(sku["supplier_lead_time_days"].dropna(), 80))

        # Group by brand (as supplier proxy)
        brand_stats = sku.groupby("brand").agg(
            avg_lt=("supplier_lead_time_days", "mean"),
            min_lt=("supplier_lead_time_days", "min"),
            max_lt=("supplier_lead_time_days", "max"),
            avg_moq=("moq_from_supplier", "mean"),
            sku_count=("sku_id", "count")
        ).reset_index()

        suppliers = []
        for _, row in brand_stats.iterrows():
            suppliers.append({
                "name": row["brand"],
                "sku_count": int(row["sku_count"]),
                "avg_lt": round(float(row["avg_lt"]), 1),
                "min_lt": int(row["min_lt"]),
                "max_lt": int(row["max_lt"]),
                "avg_moq": int(row["avg_moq"]),
            })

        # SKU details
        sku_details = []
        for _, row in sku.iterrows():
            sku_details.append({
                "sku_id": row["sku_id"],
                "brand": row["brand"],
                "product_name": row["product_name"],
                "lead_time": int(row["supplier_lead_time_days"]),
                "moq": int(row["moq_from_supplier"])
            })

        # Festive avg is estimated at 1.3x normal (based on brief's observation)
        festive_avg = round(avg_lt * 1.3, 1)

        return jsonify({
            "avg_lead_time": round(avg_lt, 1),
            "p80_lead_time": round(p80_lt, 1),
            "festive_avg_lead_time": festive_avg,
            "supplier_count": len(suppliers),
            "suppliers": suppliers,
            "sku_details": sku_details
        })
    except Exception as e:
        return jsonify({"error": str(e), "suppliers": [], "avg_lead_time": 0})

def get_data_cache():
    """Load all pipeline data once per request."""
    return {
        "report": load_json("monday_report.json"),
        "classification": load_json("classification_report.json"),
        "accuracy": load_json("forecast_accuracy.json"),
        "retro": load_json("top_14_stockout_skus.json"),
        "reorder": load_csv("reorder_recommendations.csv"),
        "sku_class": load_csv("sku_classification.csv"),
        "forecasts": load_csv("forecasts.csv"),
    }

def answer_query(msg, data):
    """Smart local chatbot that answers from pipeline data."""
    q = msg.lower().strip()
    report = data["report"]
    es = report.get("executive_summary", {}) if report else {}
    reorder = data["reorder"]
    sku_cls = data["sku_class"]
    acc = data["accuracy"] or {}
    retro = data["retro"] or {}
    cls_rpt = data["classification"] or {}
    forecasts = data["forecasts"]

    # Check for specific SKU query
    import re
    sku_match = re.search(r'sku[-\s]?(\d{2,3})', q)
    if sku_match:
        sku_num = sku_match.group(1).zfill(3)
        sku_id = f"SKU-{sku_num}"
        parts = [f"**{sku_id} Details:**\n"]
        # Reorder info
        if len(reorder) > 0:
            row = reorder[reorder["sku_id"] == sku_id]
            if len(row) > 0:
                r = row.iloc[0]
                parts.append(f"- **Product:** {r.get('product_name','N/A')} ({r.get('brand','')}, {r.get('category','')})")
                parts.append(f"- **Available Stock:** {int(r.get('available_stock',0))} units ({r.get('weeks_of_stock',0)} weeks cover)")
                parts.append(f"- **6-Week Forecast:** {int(r.get('forecast_6w_total',0))} units")
                parts.append(f"- **Reorder Qty:** {int(r.get('final_reorder_qty',0))} units (Rs.{int(r.get('order_value_inr',0)):,})")
                parts.append(f"- **Flags:** {r.get('flags','OK')}")
                if r.get('revenue_at_risk',0) > 0:
                    parts.append(f"- **Revenue at Risk:** Rs.{int(r['revenue_at_risk']):,}")
                parts.append(f"- **Reasoning:** {r.get('reason_text','')}")
        # Classification
        if len(sku_cls) > 0:
            row = sku_cls[sku_cls["sku_id"] == sku_id]
            if len(row) > 0:
                s = row.iloc[0]
                parts.append(f"- **Movement:** {s.get('movement_class','N/A')}, **ABC Class:** {s.get('abc_class','N/A')}")
                parts.append(f"- **Avg Weekly Sales:** {s.get('avg_weekly_sales',0):.0f} units")
                parts.append(f"- **Total Revenue:** Rs.{s.get('total_revenue',0):,.0f}")
        # Accuracy
        per_sku = acc.get("per_sku_mape", {})
        if sku_id in per_sku:
            info = per_sku[sku_id]
            parts.append(f"- **Forecast MAPE:** {info['mape']}% ({info['model_used']})")
        # Retro
        for s in retro.get("predicted_stockout_skus", []):
            if s["sku_id"] == sku_id:
                parts.append(f"- **Diwali Stockout Score:** {s['stockout_score']}/9 ({s['signals_triggered']})")
                parts.append(f"- **Reasoning:** {s['reasoning']}")
        return "\n".join(parts) if len(parts) > 1 else f"No data found for {sku_id}."

    # Summary / overview
    if any(w in q for w in ["summary", "overview", "report", "brief", "overall", "status", "dashboard", "hello", "hi"]):
        return f"""**Executive Summary**

- **SKUs Analyzed:** {es.get('total_skus_analyzed',0)}
- **SKUs to Reorder:** {es.get('total_skus_to_reorder',0)}
- **Total Order Value:** Rs.{es.get('total_order_value_inr',0):,}
- **Stockout Risk:** {es.get('skus_at_stockout_risk',0)} SKUs
- **Revenue at Risk:** Rs.{es.get('total_revenue_at_risk_inr', es.get('revenue_at_risk_inr',0)):,}
- **Overstock Risk:** {es.get('skus_at_overstock_risk',0)} SKUs
- **Capital Trapped:** Rs.{es.get('capital_trapped_in_overstock_inr',0):,}
- **Overall MAPE:** {acc.get('overall_mape',0)}%
- **Shelf Life Violations:** {es.get('shelf_life_violations',0)}
- **Dead Stock:** {es.get('dead_stock_count',0)} SKUs"""

    # Urgent / stockout / reorder
    if any(w in q for w in ["urgent", "stockout", "reorder", "order", "critical", "risk", "buy"]):
        urgent = report.get("urgent_orders", []) if report else []
        if not urgent and len(reorder) > 0:
            stockout_df = reorder[reorder["flags"].str.contains("STOCKOUT_RISK", na=False)].nsmallest(10, "weeks_of_stock")
            lines = ["**Urgent Reorder — Top Stockout Risk SKUs:**\n"]
            for _, r in stockout_df.iterrows():
                lines.append(f"- **{r['sku_id']}** ({r.get('product_name','')}): {r.get('weeks_of_stock',0)}w stock left, reorder **{int(r['final_reorder_qty'])}** units (Rs.{int(r.get('order_value_inr',0)):,})")
            return "\n".join(lines)
        lines = ["**Urgent Reorder — Top Stockout Risk SKUs:**\n"]
        for u in urgent[:10]:
            lines.append(f"- **{u['sku_id']}** ({u['product_name']}): {u['weeks_of_stock']}w stock, reorder **{u['reorder_qty']}** units (Rs.{u['order_value']:,})")
        lines.append(f"\n**Total Order Value:** Rs.{es.get('total_order_value_inr',0):,}")
        return "\n".join(lines)

    # Forecast / accuracy / MAPE
    if any(w in q for w in ["forecast", "accuracy", "mape", "predict", "model"]):
        per_sku = acc.get("per_sku_mape", {})
        sorted_skus = sorted(per_sku.items(), key=lambda x: x[1].get('mape',999))
        lines = [f"""**Forecast Accuracy Report**

- **Overall MAPE:** {acc.get('overall_mape',0)}%
- **LightGBM Models:** {acc.get('lgbm_count',0)}
- **Rolling Avg Fallback:** {acc.get('rolling_avg_count',0)}
- **Low Confidence SKUs:** {len(acc.get('low_confidence_skus',[]))}

**Top 5 Most Accurate:**"""]
        for sku, info in sorted_skus[:5]:
            lines.append(f"- {sku}: MAPE {info['mape']}% ({info['model_used']})")
        lines.append("\n**Bottom 5 (Least Accurate):**")
        for sku, info in sorted_skus[-5:]:
            lines.append(f"- {sku}: MAPE {info['mape']}% ({info['model_used']})")
        return "\n".join(lines)

    # Diwali / retrospective
    if any(w in q for w in ["diwali", "retro", "stockout detection", "festival"]):
        racc = retro.get("accuracy", {})
        lines = [f"""**Diwali 2023 Retrospective Analysis**

- **Correctly Identified:** {racc.get('correctly_identified',0)}/14 known stockout SKUs
- **Detection Cutoff:** Nov 7, 2023 (no lookahead bias)

**Top Predicted Stockout SKUs:**"""]
        for s in retro.get("predicted_stockout_skus", [])[:10]:
            lines.append(f"- #{s['rank']} **{s['sku_id']}** ({s['product_name']}): Score {s['stockout_score']}/9 — {s['signals_triggered']}")
        return "\n".join(lines)

    # Classification
    if any(w in q for w in ["class", "abc", "fast", "slow", "dead", "movement", "category"]):
        if len(sku_cls) > 0:
            counts = sku_cls["movement_class"].value_counts().to_dict() if "movement_class" in sku_cls.columns else {}
            abc_counts = sku_cls["abc_class"].value_counts().to_dict() if "abc_class" in sku_cls.columns else {}
            lines = ["**SKU Classification:**\n"]
            lines.append("**Movement:**")
            for k, v in counts.items():
                lines.append(f"- {k}: {v} SKUs")
            lines.append("\n**ABC Analysis:**")
            for k, v in abc_counts.items():
                lines.append(f"- {k}-class: {v} SKUs")
            return "\n".join(lines)

    # Data / classification / true zero
    if any(w in q for w in ["data", "true zero", "missing", "grid", "reconstruct", "classification report"]):
        cc = cls_rpt.get("classification_counts", {})
        return f"""**Data Classification Report**

- **Full Grid Size:** {cls_rpt.get('total_rows',0):,} rows (week x SKU x outlet)
- **Original Observed:** {cls_rpt.get('original_observed_rows',0):,}
- **Reconstructed Missing:** {cls_rpt.get('reconstructed_rows',0):,}
- **Observed:** {cc.get('observed',0):,}
- **True Zero:** {cc.get('true_zero',0):,}
- **Missing Data:** {cc.get('missing_data',0):,}
- **Stockout Gap:** {cc.get('stockout_gap',0):,}
- **Uncertain:** {cc.get('uncertain',0):,}"""

    # Overstock
    if any(w in q for w in ["overstock", "excess", "trapped", "capital"]):
        overstock = report.get("overstock_alerts", []) if report else []
        if overstock:
            lines = ["**Overstock Alerts:**\n"]
            for o in overstock:
                lines.append(f"- **{o['sku_id']}** ({o['product_name']}): {o['excess_units']} excess units, Rs.{o.get('capital_trapped',0):,} trapped")
            return "\n".join(lines)
        return f"No overstock alerts. Capital trapped in overstock: Rs.{es.get('capital_trapped_in_overstock_inr',0):,}"

    # Shelf life
    if any(w in q for w in ["shelf", "perishable", "expiry", "violation"]):
        return f"**Shelf Life Compliance:** {es.get('shelf_life_violations',0)} violations (guaranteed 0 by design). All reorder quantities are validated against shelf-life constraints before finalizing."

    # Help / what can you do
    if any(w in q for w in ["help", "what can", "how", "explain", "capability"]):
        return """**I can help you with:**

- **"Give me a summary"** — Executive overview of all KPIs
- **"Which SKUs need urgent reorder?"** — Top stockout-risk items
- **"Tell me about SKU-008"** — Full details on any specific SKU
- **"How is our forecast accuracy?"** — MAPE breakdown
- **"Show me the Diwali analysis"** — Retrospective stockout detection
- **"What's the SKU classification?"** — Movement + ABC analysis
- **"Data classification report"** — True zero vs missing data stats
- **"Any overstock risk?"** — Excess inventory alerts
- **"Shelf life violations?"** — Compliance check"""

    # Default fallback
    return """I can answer questions about your inventory data. Try asking:

- **"Summary"** — Quick overview
- **"Urgent reorders"** — Stockout risk SKUs
- **"SKU-008"** — Details on a specific SKU
- **"Forecast accuracy"** — Model performance
- **"Diwali analysis"** — Retrospective report"""

# OpenRouter API Key (Set this in your environment or .env file)
OPENROUTER_KEY = os.environ.get("OPENROUTER_KEY", "")

def build_data_context():
    """Build full data context string for the LLM."""
    d = get_data_cache()
    ctx = []
    report = d["report"] or {}
    es = report.get("executive_summary", {})
    ctx.append(f"EXECUTIVE SUMMARY: {json.dumps(es)}")
    for u in report.get("urgent_orders", [])[:10]:
        ctx.append(f"URGENT: {u['sku_id']} ({u['product_name']}): {u['weeks_of_stock']}w stock, reorder {u['reorder_qty']}, Rs.{u['order_value']:,}, {u.get('reason','')}")
    cls_rpt = d["classification"] or {}
    ctx.append(f"CLASSIFICATION: {json.dumps(cls_rpt.get('classification_counts', {}))}, total_rows={cls_rpt.get('total_rows',0)}, observed={cls_rpt.get('original_observed_rows',0)}, reconstructed={cls_rpt.get('reconstructed_rows',0)}")
    acc = d["accuracy"] or {}
    ctx.append(f"ACCURACY: overall_mape={acc.get('overall_mape',0)}%, lgbm={acc.get('lgbm_count',0)}, rolling={acc.get('rolling_avg_count',0)}")
    for sku, info in (acc.get("per_sku_mape", {})).items():
        ctx.append(f"  {sku}: MAPE={info['mape']}% ({info['model_used']})")
    reorder = d["reorder"]
    if len(reorder) > 0:
        for _, r in reorder.iterrows():
            ctx.append(f"REORDER {r['sku_id']} ({r.get('product_name','')}): stock={r.get('available_stock',0)}, {r.get('weeks_of_stock',0)}w, forecast_6w={r.get('forecast_6w_total',0)}, qty={r.get('final_reorder_qty',0)}, Rs.{r.get('order_value_inr',0):,.0f}, flags={r.get('flags','')}, rev_risk={r.get('revenue_at_risk',0)}, reason:{r.get('reason_text','')}")
    sku_cls = d["sku_class"]
    if len(sku_cls) > 0:
        for _, s in sku_cls.iterrows():
            ctx.append(f"SKU_CLASS {s['sku_id']} ({s.get('product_name','')}): {s.get('movement_class','')}, ABC={s.get('abc_class','')}, avg_weekly={s.get('avg_weekly_sales',0):.0f}, revenue=Rs.{s.get('total_revenue',0):,.0f}")
    retro = d["retro"] or {}
    racc = retro.get("accuracy", {})
    ctx.append(f"DIWALI RETRO: correct={racc.get('correctly_identified',0)}/14, missed={racc.get('missed_skus',[])}")
    for s in retro.get("predicted_stockout_skus", []):
        ctx.append(f"  #{s['rank']} {s['sku_id']} ({s['product_name']}): score={s['stockout_score']}/9, signals={s['signals_triggered']}, {s['reasoning']}")
    return "\n".join(ctx)

@app.route("/api/chat", methods=["POST"])
def api_chat():
    data_req = request.get_json()
    user_msg = data_req.get("message", "")
    history = data_req.get("history", [])
    if not user_msg:
        return jsonify({"error": "No message"}), 400

    # Try OpenRouter AI first
    try:
        context = build_data_context()
        system_prompt = f"""You are the Sunrise Demand AI Assistant for Sunrise Consumer Goods (FMCG distributor, Pune & Nashik, 320 outlets, 40 SKUs).

You have COMPLETE access to all pipeline data below. Answer accurately using this data. Be concise, use bullet points and bold for key numbers. Format currency as Rs. with commas. When asked about a specific SKU, provide ALL available details.

=== PIPELINE DATA ===
{context}
=== END DATA ==="""

        messages = [{"role": "system", "content": system_prompt}]
        for h in history[-6:]:
            messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
        messages.append({"role": "user", "content": user_msg})

        resp = http_requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"},
            json={"model": "google/gemini-2.0-flash-001", "messages": messages, "max_tokens": 1500, "temperature": 0.3},
            timeout=30
        )
        result = resp.json()
        reply = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        if reply:
            return jsonify({"reply": reply})
    except Exception:
        pass

    # Fallback to local engine
    pipeline_data = get_data_cache()
    reply = answer_query(user_msg, pipeline_data)
    return jsonify({"reply": reply})

# ── Batch Expiry Tracking (Brief Part 2H / Phase 12) ──
@app.route("/batch-expiry")
@login_required
def batch_expiry():
    return render_template("batch_expiry.html", page="batch_expiry")

@app.route("/api/batch-expiry")
def api_batch_expiry():
    """Batch expiry data from inventory snapshot."""
    try:
        inv = load_csv("inventory_snapshot.csv", DATA)
        sku = load_csv("sku_master.csv", DATA)
        if len(inv) == 0 or len(sku) == 0:
            return jsonify([])
        merged = inv.merge(sku[["sku_id","product_name","brand","category","shelf_life_days"]], on="sku_id", how="left")
        # Simulate batch expiry based on shelf_life_days and last_receipt_date
        import numpy as np
        from datetime import datetime, timedelta
        results = []
        for _, r in merged.iterrows():
            shelf = int(r.get("shelf_life_days", 365))
            stock = int(r.get("warehouse_stock", 0))
            if stock <= 0:
                continue
            # Simulate batch with receipt date ~ 30 days ago
            receipt = datetime.now() - timedelta(days=np.random.randint(10, shelf//2))
            expiry = receipt + timedelta(days=shelf)
            days_to_expiry = (expiry - datetime.now()).days
            status = "expired" if days_to_expiry < 0 else "critical" if days_to_expiry < 14 else "warning" if days_to_expiry < 30 else "ok"
            results.append({
                "sku_id": r["sku_id"], "product_name": r.get("product_name",""),
                "brand": r.get("brand",""), "category": r.get("category",""),
                "batch_no": f"B-{r['sku_id'][-3:]}-{receipt.strftime('%m%d')}",
                "qty": stock, "mfd_date": receipt.strftime("%Y-%m-%d"),
                "expiry_date": expiry.strftime("%Y-%m-%d"),
                "days_to_expiry": days_to_expiry, "status": status,
                "shelf_life_days": shelf
            })
        return jsonify(sorted(results, key=lambda x: x["days_to_expiry"]))
    except Exception as e:
        return jsonify({"error": str(e)})

# ── Purchase Orders (Brief Part 6A — GST Compliance) ──
@app.route("/purchase-orders")
@login_required
def purchase_orders():
    return render_template("purchase_orders.html", page="purchase_orders")

@app.route("/api/generate-po", methods=["POST"])
def api_generate_po():
    """Generate GST-compliant purchase order from reorder recommendations."""
    try:
        from datetime import datetime, timedelta
        data = request.json or {}
        sku_ids = data.get("sku_ids", [])
        reorder = load_csv("reorder_recommendations.csv")
        sku_master = load_csv("sku_master.csv", DATA)
        if len(reorder) == 0:
            return jsonify({"status":"error","message":"No reorder data"})
        if sku_ids:
            selected = reorder[reorder["sku_id"].isin(sku_ids)]
        else:
            selected = reorder[reorder["final_reorder_qty"] > 0]
        po_items = []
        for _, r in selected.iterrows():
            ski = sku_master[sku_master["sku_id"]==r["sku_id"]]
            cost = float(ski.iloc[0]["cost_price"]) if len(ski) > 0 else 0
            gst_rate = 18.0  # Default GST
            qty = int(r["final_reorder_qty"])
            base = cost * qty
            cgst = base * (gst_rate/2) / 100
            sgst = base * (gst_rate/2) / 100
            po_items.append({
                "sku_id": r["sku_id"], "product_name": r.get("product_name",""),
                "qty": qty, "unit_price": cost,
                "base_amount": round(base, 2),
                "cgst_rate": gst_rate/2, "cgst_amount": round(cgst, 2),
                "sgst_rate": gst_rate/2, "sgst_amount": round(sgst, 2),
                "total": round(base + cgst + sgst, 2)
            })
        po_number = f"PO-{datetime.now().strftime('%Y%m%d')}-{len(po_items):03d}"
        total_base = sum(i["base_amount"] for i in po_items)
        total_tax = sum(i["cgst_amount"]+i["sgst_amount"] for i in po_items)
        return jsonify({
            "status": "success",
            "po_number": po_number,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "items": po_items,
            "subtotal": round(total_base, 2),
            "total_tax": round(total_tax, 2),
            "grand_total": round(total_base + total_tax, 2),
            "item_count": len(po_items)
        })
    except Exception as e:
        return jsonify({"status":"error","message":str(e)})

# ── Data Upload with Validation (Brief Part 2D) ──
@app.route("/api/upload-sales", methods=["POST"])
def api_upload_sales():
    """Upload weekly sales CSV with validation."""
    try:
        file = request.files.get("file")
        if not file:
            return jsonify({"status":"error","message":"No file"})
        upload_dir = os.path.join(ROOT, "data", "uploads")
        os.makedirs(upload_dir, exist_ok=True)
        filepath = os.path.join(upload_dir, file.filename)
        file.save(filepath)
        from ingestion import validate_sales_upload, save_validated_upload
        is_valid, cleaned_df, report = validate_sales_upload(filepath)
        if is_valid:
            save_validated_upload(cleaned_df)
        return jsonify({"status":"success" if is_valid else "error", "report": report})
    except Exception as e:
        return jsonify({"status":"error","message":str(e)})

# ── Barcode Scanner PWA (Brief Phase 10) ──
@app.route("/mobile/")
@app.route("/mobile/<path:filename>")
def mobile_pwa(filename="index.html"):
    """Serve the barcode scanner PWA."""
    mobile_dir = os.path.join(ROOT, "mobile")
    return send_file(os.path.join(mobile_dir, filename))

@app.route("/api/sku/scan", methods=["POST"])
def api_sku_scan():
    """Receive barcode scan from PWA."""
    try:
        data = request.json
        sku_code = data.get("sku_code", "").strip()
        if not sku_code:
            return jsonify({"status": "error", "message": "SKU code required"})
        # Log the scan
        scan_log_path = os.path.join(DATA, "processed", "scan_log.json")
        scans = []
        if os.path.exists(scan_log_path):
            with open(scan_log_path) as f:
                scans = json.load(f)
        scans.append({
            "sku_code": sku_code,
            "product_name": data.get("product_name", ""),
            "qty_received": data.get("qty_received", 1),
            "batch_expiry": data.get("batch_expiry", ""),
            "scanned_at": data.get("scanned_at", ""),
            "synced_at": pd.Timestamp.now().isoformat()
        })
        os.makedirs(os.path.dirname(scan_log_path), exist_ok=True)
        with open(scan_log_path, "w") as f:
            json.dump(scans, f, indent=2)
        return jsonify({"status": "success", "message": f"Scan recorded: {sku_code}"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

# ── Inventory Audit Trail (Brief Phase 14) ──
@app.route("/audit-trail")
@login_required
def audit_trail():
    return render_template("audit_trail.html", page="audit_trail")

@app.route("/api/audit-trail")
def api_audit_trail():
    """Return inventory adjustment audit log."""
    log_path = os.path.join(PROCESSED, "audit_trail.json")
    if os.path.exists(log_path):
        with open(log_path) as f:
            return jsonify(json.load(f))
    return jsonify([])

@app.route("/api/audit-trail/add", methods=["POST"])
def api_audit_add():
    """Record an inventory adjustment."""
    try:
        data = request.json
        log_path = os.path.join(PROCESSED, "audit_trail.json")
        entries = []
        if os.path.exists(log_path):
            with open(log_path) as f:
                entries = json.load(f)
        entries.append({
            "timestamp": pd.Timestamp.now().isoformat(),
            "user": current_user.full_name if current_user.is_authenticated else "System",
            "sku_id": data.get("sku_id", ""),
            "field": data.get("field", "warehouse_stock"),
            "old_value": data.get("old_value", 0),
            "new_value": data.get("new_value", 0),
            "reason": data.get("reason", "")
        })
        with open(log_path, "w") as f:
            json.dump(entries, f, indent=2)
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

# ── Context processor: inject globals into all templates (Phase 14) ──
@app.context_processor
def inject_globals():
    ai_mode = "openrouter" if OPENROUTER_KEY else "local"
    return {
        "ai_mode": ai_mode,
        "app_version": "2.0.0"
    }

if __name__ == "__main__":
    os.makedirs("logs", exist_ok=True)
    ensure_pipeline()
    app.run(debug=True, host="0.0.0.0", port=5000)
