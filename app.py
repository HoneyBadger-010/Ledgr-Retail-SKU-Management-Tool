"""
app.py — Flask web application with Tabler UI dashboard
Serves API endpoints and HTML pages for the demand forecasting system.
Integrated with Flask-Login auth (Brief Part 2B) and PostgreSQL (Phase 1).
"""
import os, sys, json, threading
from datetime import datetime, timedelta
import pandas as pd
import requests as http_requests
from flask import Flask, render_template, jsonify, request, send_file
from flask_login import login_required, current_user

# Load .env so OPENROUTER_KEY / TWILIO / MAIL / etc. surface as os.environ.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), override=False)
except Exception:
    pass

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "sunrise-dev-key-change-in-prod")
if app.secret_key == "sunrise-dev-key-change-in-prod" and os.environ.get("FLASK_ENV") == "production":
    raise RuntimeError("FLASK_SECRET_KEY must be set in production (refusing to start with default key)")

# Production security headers
if os.environ.get("FLASK_ENV") == "production":
    app.config.update(
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    )
    
    @app.after_request
    def set_security_headers(response):
        """Add security headers for production"""
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net; "
            "img-src 'self' data: https:; "
            "connect-src 'self' https://world.openfoodfacts.org; "
            "frame-ancestors 'self';"
        )
        return response

# Auth integration (Brief Part 2B) — register blueprint BEFORE init_csrf so
# the login route is in the view_functions registry when CSRF check runs.
from auth import auth_bp, init_auth, init_csrf, role_required
app.register_blueprint(auth_bp)
init_auth(app)
init_csrf(app)

# Database integration (Brief Phase 1 — PostgreSQL)
from database import init_db, db
init_db(app)

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

@app.route("/sku-performance")
@login_required
def sku_performance():
    return render_template("sku_performance.html", page="sku_performance")

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
@login_required
def api_report():
    return jsonify(load_json("monday_report.json"))

@app.route("/api/stockout-analysis")
@login_required
def api_stockout():
    df = load_csv("diwali_stockout_analysis.csv")
    return jsonify(df.head(40).to_dict(orient="records"))

@app.route("/api/top14")
@login_required
def api_top14():
    return jsonify(load_json("top_14_stockout_skus.json"))

@app.route("/api/forecasts")
@login_required
def api_forecasts():
    df = load_csv("forecasts.csv")
    sku = request.args.get("sku")
    if sku:
        df = df[df["sku_id"] == sku]
    return jsonify(df.to_dict(orient="records"))

@app.route("/api/forecast-accuracy")
@login_required
def api_forecast_accuracy():
    """Phase 8 fix: Read from DB rolling MAPE, fallback to JSON."""
    from database import get_forecast_accuracy_from_db
    return jsonify(get_forecast_accuracy_from_db())

@app.route("/api/reorder-recommendations")
@login_required
def api_reorder_recs():
    df = load_csv("reorder_recommendations.csv")
    flag = request.args.get("flag")
    if flag and flag != "All":
        df = df[df["flags"].str.contains(flag, na=False)]
    return jsonify(df.to_dict(orient="records"))

@app.route("/api/sku-classification")
@login_required
def api_sku_class():
    df = load_csv("sku_classification.csv")
    return jsonify(df.to_dict(orient="records"))

@app.route("/api/sku-list")
@login_required
def api_sku_list():
    from database import get_sku_list
    from auth import get_user_store_ids
    return jsonify(get_sku_list(store_ids=get_user_store_ids()))

@app.route("/api/sku-sales/<sku_id>")
@login_required
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
@login_required
def api_class_report():
    return jsonify(load_json("classification_report.json"))

@app.route("/api/run-pipeline", methods=["POST"])
@login_required
@role_required("owner", "manager")
def api_run_pipeline():
    """Run pipeline asynchronously, persisting progress to pipeline_runs
    (Brief Phase 7 — DB-backed status survives Gunicorn worker hops)."""
    from database import start_pipeline_run, update_pipeline_step, finish_pipeline_run, get_latest_pipeline_run
    latest = get_latest_pipeline_run()
    if latest.get("running"):
        return jsonify({"status": "already_running", "message": "Pipeline is already running"})

    run_id = start_pipeline_run()
    captured_app = app

    def run_async(rid):
        with captured_app.app_context():
            try:
                sys.path.insert(0, ROOT)
                from pipeline import run_pipeline

                def cb(step_idx, step_name):
                    try:
                        with captured_app.app_context():
                            update_pipeline_step(rid, step_idx)
                    except Exception:
                        pass

                run_pipeline(progress_cb=cb)
                finish_pipeline_run(rid, success=True)
            except Exception as e:
                try:
                    finish_pipeline_run(rid, success=False, error_message=str(e))
                except Exception:
                    pass

    t = threading.Thread(target=run_async, args=(run_id,), daemon=True)
    t.start()
    return jsonify({"status": "started", "run_id": run_id, "message": "Pipeline started in background"})

@app.route("/api/pipeline-status")
@login_required
def api_pipeline_status():
    from database import get_latest_pipeline_run
    return jsonify(get_latest_pipeline_run())

@app.route("/api/download-reorder")
@login_required
def download_reorder():
    path = os.path.join(PROCESSED, "reorder_recommendations.csv")
    if os.path.exists(path):
        return send_file(path, as_attachment=True, download_name="reorder_plan.csv")
    return "File not found", 404

# ── SKU Management API (Brief Part 2F) ──
@app.route("/api/sku-list-full")
@login_required
def api_sku_list_full():
    """Full SKU master data for the management table (DB-backed, Brief C8)."""
    try:
        from database import get_sku_list_full
        from auth import get_user_store_ids
        return jsonify(get_sku_list_full(store_ids=get_user_store_ids()))
    except Exception as e:
        # Fall back to CSV only if DB not available (e.g. tests)
        df = load_csv("sku_master.csv", DATA)
        return jsonify(df.to_dict(orient="records"))

@app.route("/api/sku/create", methods=["POST"])
@login_required
@role_required("owner", "manager")
def api_sku_create():
    """Add a new SKU to the database (Phase 1 fix). Scoped to the user's
    primary store (first in their store list)."""
    try:
        from database import create_sku
        from auth import get_user_store_ids
        stores = get_user_store_ids() or []
        store_id = stores[0] if stores else None
        if not store_id:
            return jsonify({"status": "error", "message": "User has no store assignment"}), 403
        ok, msg = create_sku(request.json, store_id=store_id)
        return jsonify({"status": "success" if ok else "error", "message": msg})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route("/api/sku/delete", methods=["POST"])
@login_required
@role_required("owner")
def api_sku_delete():
    """Delete a SKU from the database (Phase 1 fix), scoped to the user's stores."""
    try:
        from database import delete_sku
        from auth import get_user_store_ids
        ok, msg = delete_sku(request.json.get("sku_id", "").strip(),
                             store_ids=get_user_store_ids())
        return jsonify({"status": "success" if ok else "error", "message": msg})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route("/api/sku/upload", methods=["POST"])
@login_required
@role_required("owner", "manager")
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
@app.route("/api/outlet-sales/<outlet_id>")
@login_required
def api_outlet_sales(outlet_id):
    """Per-outlet weekly sales aggregate, used by the outlets detail panel."""
    sales = load_csv("sales_classified.csv")
    if len(sales) == 0:
        return jsonify([])
    sales["week_start_date"] = pd.to_datetime(sales["week_start_date"])
    df = sales[sales["outlet_id"] == outlet_id].groupby("week_start_date")["units_sold"].sum().reset_index()
    df = df.sort_values("week_start_date")
    df["week_start_date"] = df["week_start_date"].dt.strftime("%Y-%m-%d")
    return jsonify(df.to_dict(orient="records"))


@app.route("/api/outlets")
@login_required
def api_outlets():
    """Outlets list scoped to user's stores. Replaces the hardcoded 10-row
    array that was in outlets.html."""
    try:
        from database import get_outlet_list
        from auth import get_user_store_ids
        outlets = get_outlet_list(store_ids=get_user_store_ids())
        # Augment with sales volume from the classified pipeline output, when available.
        sales_path = os.path.join(PROCESSED, "sales_classified.csv")
        sales_by_outlet = {}
        if os.path.exists(sales_path):
            try:
                df = pd.read_csv(sales_path, usecols=["outlet_id", "units_sold", "week_start_date"])
                df["week_start_date"] = pd.to_datetime(df["week_start_date"], errors="coerce")
                last8 = df["week_start_date"].max() - pd.Timedelta(weeks=8)
                recent = df[df["week_start_date"] >= last8]
                sales_by_outlet = recent.groupby("outlet_id")["units_sold"].sum().to_dict()
            except Exception:
                sales_by_outlet = {}
        for o in outlets:
            o["weekly_units"] = int(sales_by_outlet.get(o["outlet_id"], 0) // 8) if o["outlet_id"] in sales_by_outlet else 0
        return jsonify(outlets)
    except Exception as e:
        return jsonify({"error": str(e), "outlets": []})


@app.route("/api/dashboard-summary")
@login_required
def api_dashboard_summary():
    """One-shot endpoint for the Dashboard page — saves 3 requests and lets
    overview.html drop its hardcoded fallbacks. Returns executive summary,
    pipeline run status, alert counts, and a real WhatsApp send history."""
    try:
        from database import get_latest_pipeline_run
        from auth import get_user_store_ids
        report = load_json("monday_report.json")
        es = (report or {}).get("executive_summary", {})
        accuracy = load_json("forecast_accuracy.json")

        reorder_df = load_csv("reorder_recommendations.csv")
        forecasts_df = load_csv("forecasts.csv")

        urgent_count = 0
        revenue_at_risk = 0
        top_risk = None
        if not reorder_df.empty:
            stockouts = reorder_df[reorder_df["flags"].fillna("").str.contains("STOCKOUT_RISK")]
            urgent_count = int(len(stockouts))
            revenue_at_risk = int(stockouts["revenue_at_risk"].sum()) if "revenue_at_risk" in stockouts else 0
            if len(stockouts) > 0:
                top = stockouts.nsmallest(1, "weeks_of_stock").iloc[0]
                top_risk = {
                    "sku_id": str(top.get("sku_id", "")),
                    "product_name": str(top.get("product_name", "")),
                    "weeks_of_stock": float(top.get("weeks_of_stock", 0) or 0),
                    "revenue_at_risk": int(top.get("revenue_at_risk", 0) or 0),
                }

        # Real forecast trajectory (sum of all-SKU weekly forecasts) for the
        # 6-week horizon — drives the dashboard mini sparkline.
        weekly_totals = []
        if not forecasts_df.empty and "week_start_date" in forecasts_df.columns:
            try:
                weekly_totals = (forecasts_df.groupby("week_start_date")["forecasted_units"]
                                 .sum().sort_index().astype(int).tolist())
            except Exception:
                weekly_totals = []

        return jsonify({
            "executive_summary": es,
            "pipeline": get_latest_pipeline_run(),
            "accuracy": {
                "overall_mape": (accuracy or {}).get("overall_mape", 0),
                "lgbm_count": (accuracy or {}).get("lgbm_count", 0),
                "low_confidence_skus": (accuracy or {}).get("low_confidence_skus", []),
            },
            "urgent_count": urgent_count,
            "revenue_at_risk": revenue_at_risk,
            "top_risk": top_risk,
            "forecast_weekly_totals": weekly_totals,
            "report_date": (report or {}).get("report_date"),
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/data-quality")
@login_required
def api_data_quality():
    """Data quality metrics from the classified sales data."""
    try:
        sales = load_csv("sales_classified.csv")
        if len(sales) == 0:
            return jsonify({"accepted_rows": 0, "rejected_rows": 0, "acceptance_rate": 0})

        # Classification breakdown
        cls_counts = sales["row_classification"].value_counts().to_dict()
        # Acceptance rate is computed over rows that were actually collected
        # (observed + explicitly-classified). missing_data covers the 1.9M
        # grid reconstruction where the outlet never reported — counting that
        # as "rejected" tanks the rate to ~4% and is misleading.
        accepted_classes = ["observed", "true_zero"]
        rejected_classes = ["stockout_gap", "uncertain_excluded"]
        missing_classes = ["missing_data"]
        accepted = sum(cls_counts.get(c, 0) for c in accepted_classes)
        rejected = sum(cls_counts.get(c, 0) for c in rejected_classes)
        missing = sum(cls_counts.get(c, 0) for c in missing_classes)
        denom = accepted + rejected
        rate = (accepted / denom * 100) if denom > 0 else 0

        # Weekly stats for chart (simulate from sales data by week)
        sales["week_start_date"] = pd.to_datetime(sales["week_start_date"])
        weeks = sorted(sales["week_start_date"].unique())
        last_8_weeks = weeks[-8:] if len(weeks) >= 8 else weeks

        weekly_accepted = []
        weekly_rejected = []
        weekly_labels = []
        for w in last_8_weeks:
            wk_data = sales[sales["week_start_date"] == w]
            # Per-week chart also excludes missing_data so the bars are visible.
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
            "missing_rows": missing,
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
@login_required
def api_supplier_performance():
    """Phase 9 fix: Supplier lead times from DB with P80 calculation."""
    try:
        from database import get_supplier_lead_times
        from auth import get_user_store_ids
        return jsonify(get_supplier_lead_times(store_ids=get_user_store_ids()))
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


def _live_counts():
    """Read SKU and outlet counts from the actual data (D4 fix — no more
    hardcoded '40 SKUs' / '140 SKUs' string drift)."""
    try:
        sku_path = os.path.join(DATA, "sku_master.csv")
        outlet_path = os.path.join(DATA, "outlet_master.csv")
        sku_count = 0
        outlet_count = 0
        if os.path.exists(sku_path):
            sku_count = max(0, sum(1 for _ in open(sku_path)) - 1)
        if os.path.exists(outlet_path):
            outlet_count = max(0, sum(1 for _ in open(outlet_path)) - 1)
        return sku_count, outlet_count
    except Exception:
        return 0, 0

def build_data_context(query=None):
    """Build retrieval-based context string for the LLM (Brief Phase 11).
    If a query is supplied, returns only the top-k most relevant chunks plus
    the executive summary; otherwise falls back to a short summary blob.
    Replaces the old "dump everything" pattern that broke at 140 SKUs."""
    try:
        from rag import build_context
        if query:
            # Larger k + char budget so multi-domain questions (e.g.
            # "best outlet last week" + "highest stockout SKU") get
            # both relevant outlet and SKU chunks.
            ctx = build_context(query, k=10, max_chars=10000)
            if ctx:
                return ctx
    except Exception as e:
        print(f"[chat] RAG retrieval failed, falling back to summary: {e}")
    # Fallback: summary only, no per-SKU dump
    d = get_data_cache()
    rep = d.get("report") or {}
    es = rep.get("executive_summary", {})
    return f"EXECUTIVE_SUMMARY: {json.dumps(es)}"

@app.route("/api/chat", methods=["POST"])
@login_required
def api_chat():
    data_req = request.get_json()
    user_msg = data_req.get("message", "")
    history = data_req.get("history", [])
    if not user_msg:
        return jsonify({"error": "No message"}), 400

    # Try OpenRouter AI first (only if API key is configured)
    if OPENROUTER_KEY:
        try:
            context = build_data_context(query=user_msg)
            # Pull live counts so the system prompt isn't lying about scale.
            sku_count, outlet_count = _live_counts()
            system_prompt = f"""You are Ledgr, the demand-forecasting AI assistant for Sunrise Consumer Goods (FMCG distributor, Pune & Nashik, {outlet_count} outlets, {sku_count} SKUs).

You have access to the full operational data of the system. You can answer questions about ANY of these areas:

• **SKUs / Inventory**: per-SKU forecasts, available stock, weeks of cover, reorder qty, MOQ, lead times, MAPE, stockout/overstock flags, dead stock, expiry alerts, classification (movement_class, ABC).
• **Outlets**: per-outlet sales leaderboards (last week, last 8 weeks), channel rollups (kirana / supermarket / medical), outlet metadata, outlets that missed reporting.
• **Suppliers**: per-supplier average lead time, P80 worst-case, festive Oct-Nov estimate, lead-time variance.
• **Purchase Orders**: PO status (draft / approved / received), supplier grouping, intrastate (CGST+SGST) vs interstate (IGST), recent PO history.
• **Pipeline**: when the last run completed, current status, step reached, any errors.
• **Data Quality**: accepted / rejected / never-collected rows, acceptance rate, classification breakdown.
• **Diwali Retrospective**: recall on the known-14 stockout SKUs, missed SKUs, false positives, signals triggered.
• **Batch Expiry**: critical (<14 days), warning (14-30 days), healthy batches.
• **Forecasts**: 6-week horizon by week.

Read the data below carefully and answer the user's question directly using whatever sections are present. The retrieval system has already pulled the most relevant sections for this query — if a section is below, you have access to that data, so use it to answer (do NOT say "I don't have access" if the section is present). Quote specific SKU IDs, outlet IDs, supplier names, and exact numbers from the data. Be concise — use bullet points and bold for key numbers. Format currency as Rs. with commas. "Last week" means the most recent week in the data set (not real-world last week). Only say data is missing if a section is genuinely not in the context below.

=== DATA RETRIEVED FOR THIS QUERY ===
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
        except Exception as e:
            print(f"[chat] OpenRouter failed: {e}, falling back to local engine")

    # Fallback to local engine (works without API key)
    pipeline_data = get_data_cache()
    reply = answer_query(user_msg, pipeline_data)
    return jsonify({"reply": reply})

# ── Batch Expiry Tracking (Brief Part 2H / Phase 12) ──
@app.route("/batch-expiry")
@login_required
def batch_expiry():
    return render_template("batch_expiry.html", page="batch_expiry")

@app.route("/api/batch-expiry")
@login_required
def api_batch_expiry():
    """Phase 12 fix: Real batch expiry from DB, not np.random."""
    try:
        from database import get_batch_expiry
        from auth import get_user_store_ids
        return jsonify(get_batch_expiry(store_ids=get_user_store_ids()))
    except Exception as e:
        return jsonify({"error": str(e)})

# ── Purchase Orders (Brief Part 6A — GST Compliance) ──
@app.route("/purchase-orders")
@login_required
def purchase_orders():
    return render_template("purchase_orders.html", page="purchase_orders")

@app.route("/api/generate-po", methods=["POST"])
@login_required
@role_required("owner")
def api_generate_po():
    """Brief Part 6A: GST-compliant purchase order generation.
    - Reads sku.gst_rate / hsn_code from DB (no hardcoded 18%).
    - Groups items by supplier so one PO = one supplier (real-world).
    - Detects interstate vs intrastate via store.state vs supplier_state →
      emits IGST or CGST+SGST accordingly.
    - Persists each PO to the purchase_orders table with PO-YYYYMMDD-NNN
      sequencing per day per store.
    - Items missing required GST fields (HSN code, supplier name, gstin)
      are returned as a 'blocked_items' list so the UI can prompt the user
      to fill them in SKU management before re-attempting."""
    try:
        from datetime import date, datetime as _dt, timedelta as _td
        from models import PurchaseOrder, POStatus, SKU, Store
        from auth import get_user_store_ids

        data = request.json or {}
        requested_skus = data.get("sku_ids", [])
        reorder = load_csv("reorder_recommendations.csv")
        if len(reorder) == 0:
            return jsonify({"status": "error", "message": "No reorder data — run pipeline first"})

        if requested_skus:
            target_codes = [s for s in requested_skus]
        else:
            target_codes = reorder[reorder["final_reorder_qty"] > 0]["sku_id"].astype(str).tolist()
        if not target_codes:
            return jsonify({"status": "error", "message": "No SKUs need reorder right now"})

        store_ids = get_user_store_ids() or []
        if not store_ids:
            return jsonify({"status": "error", "message": "User has no store assignment"}), 403
        primary_store = Store.query.get(store_ids[0])
        if not primary_store:
            return jsonify({"status": "error", "message": "Primary store not found"})
        buyer_state = (primary_store.state or "Maharashtra").strip().lower()

        skus = SKU.query.filter(SKU.sku_code.in_(target_codes),
                                SKU.store_id.in_(store_ids)).all()
        sku_by_code = {s.sku_code: s for s in skus}

        # Group selected SKUs by supplier_name (one PO per supplier)
        groups = {}
        blocked = []
        for code in target_codes:
            sku = sku_by_code.get(code)
            if not sku:
                continue
            qty_row = reorder[reorder["sku_id"] == code]
            if len(qty_row) == 0:
                continue
            qty = int(qty_row.iloc[0]["final_reorder_qty"])
            if qty <= 0:
                continue
            missing = []
            if not sku.hsn_code: missing.append("hsn_code")
            if not sku.supplier_name: missing.append("supplier_name")
            if not sku.supplier_gstin: missing.append("supplier_gstin")
            if not sku.gst_rate: missing.append("gst_rate")
            if missing:
                blocked.append({"sku_id": code, "missing_fields": missing})
                continue
            groups.setdefault(sku.supplier_name, []).append((sku, qty, qty_row.iloc[0]))

        if blocked and not groups:
            return jsonify({
                "status": "error",
                "message": f"{len(blocked)} SKU(s) blocked from PO generation — fill GST/supplier fields in SKU Management first.",
                "blocked_items": blocked
            }), 400

        today = _dt.utcnow().date()
        date_prefix = today.strftime("%Y%m%d")
        # Find max sequence used today for this store
        existing_today = PurchaseOrder.query.filter(
            PurchaseOrder.po_number.like(f"PO-{date_prefix}-%"),
            PurchaseOrder.store_id == primary_store.id
        ).all()
        seq = max((int(p.po_number.split("-")[-1]) for p in existing_today), default=0)

        purchase_orders = []
        for supplier_name, items in groups.items():
            sample_sku = items[0][0]
            supplier_state = (sample_sku.supplier_state or buyer_state).strip().lower()
            interstate = supplier_state != buyer_state
            seq += 1
            po_number = f"PO-{date_prefix}-{seq:03d}"

            po_items = []
            total_base = 0.0
            total_tax = 0.0
            for sku, qty, _row in items:
                cost = float(sku.cost_price or 0)
                rate = float(sku.gst_rate or 0)
                base = round(cost * qty, 2)
                if interstate:
                    igst = round(base * rate / 100, 2)
                    cgst = sgst = 0.0
                    item_tax = igst
                else:
                    cgst = round(base * (rate / 2) / 100, 2)
                    sgst = round(base * (rate / 2) / 100, 2)
                    igst = 0.0
                    item_tax = cgst + sgst
                total_base += base
                total_tax += item_tax
                po_items.append({
                    "sku_id": sku.sku_code, "product_name": sku.product_name,
                    "hsn_code": sku.hsn_code, "qty": qty,
                    "unit_price": cost, "base_amount": base,
                    "gst_rate": rate,
                    "cgst_rate": rate/2 if not interstate else 0,
                    "cgst_amount": cgst,
                    "sgst_rate": rate/2 if not interstate else 0,
                    "sgst_amount": sgst,
                    "igst_rate": rate if interstate else 0,
                    "igst_amount": igst,
                    "total": round(base + item_tax, 2),
                })
                # Persist one DB row per item (PO is logical grouping by po_number)
                po_row = PurchaseOrder(
                    po_number=po_number,
                    created_date=today,
                    sku_id=sku.id,
                    qty_ordered=qty,
                    unit_price=cost,
                    total_value=round(base + item_tax, 2),
                    hsn_code=sku.hsn_code,
                    supplier_name=sku.supplier_name,
                    supplier_gstin=sku.supplier_gstin,
                    cgst_rate=rate/2 if not interstate else 0,
                    sgst_rate=rate/2 if not interstate else 0,
                    igst_rate=rate if interstate else 0,
                    po_status=POStatus.DRAFT,
                    store_id=primary_store.id,
                )
                db.session.add(po_row)

            purchase_orders.append({
                "po_number": po_number,
                "date": today.isoformat(),
                "supplier_name": supplier_name,
                "supplier_gstin": sample_sku.supplier_gstin,
                "supplier_state": sample_sku.supplier_state,
                "buyer_store": primary_store.name,
                "buyer_state": primary_store.state,
                "buyer_gstin": primary_store.gstin,
                "is_interstate": interstate,
                "tax_type": "IGST" if interstate else "CGST+SGST",
                "items": po_items,
                "subtotal": round(total_base, 2),
                "total_tax": round(total_tax, 2),
                "grand_total": round(total_base + total_tax, 2),
                "item_count": len(po_items),
            })
        db.session.commit()

        return jsonify({
            "status": "success",
            "purchase_orders": purchase_orders,
            "po_count": len(purchase_orders),
            "blocked_items": blocked,
            # Backwards-compat fields for older UI that expects a single PO:
            "po_number": purchase_orders[0]["po_number"] if purchase_orders else "",
            "date": today.isoformat(),
            "items": purchase_orders[0]["items"] if purchase_orders else [],
            "subtotal": purchase_orders[0]["subtotal"] if purchase_orders else 0,
            "total_tax": purchase_orders[0]["total_tax"] if purchase_orders else 0,
            "grand_total": purchase_orders[0]["grand_total"] if purchase_orders else 0,
            "item_count": purchase_orders[0]["item_count"] if purchase_orders else 0,
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"status": "error", "message": str(e)})

# ── Data Upload with Validation (Brief Part 2D) ──
@app.route("/api/upload-sales", methods=["POST"])
@login_required
@role_required("owner", "manager")
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
# ── Purchase Order Management APIs ──

@app.route("/api/orders/approve", methods=["POST"])
@login_required
@role_required("owner")
def api_orders_approve():
    """Approve selected reorder recommendations → create PurchaseOrders."""
    try:
        from models import PurchaseOrder, POStatus, SKU
        items = request.json.get("items", [])
        if not items:
            return jsonify({"status": "error", "message": "No items to approve"})
        created = []
        today = datetime.utcnow()
        for item in items:
            sku = SKU.query.filter_by(sku_code=item.get("sku_id", "")).first()
            if not sku:
                continue
            po_num = f"PO-{today.strftime('%Y%m%d')}-{len(created)+1:03d}"
            po = PurchaseOrder(
                po_number=po_num,
                created_date=today.date(),
                sku_id=sku.id,
                qty_ordered=int(item.get("qty", 0)),
                unit_price=float(item.get("cost_price", sku.cost_price or 0)),
                total_value=float(item.get("qty", 0)) * float(item.get("cost_price", sku.cost_price or 0)),
                supplier_name=item.get("vendor", ""),
                po_status=POStatus.APPROVED,
                store_id='store-pune-001'
            )
            db.session.add(po)
            created.append(po_num)
        db.session.commit()
        return jsonify({"status": "success", "message": f"{len(created)} orders approved", "po_numbers": created})
    except Exception as e:
        db.session.rollback()
        return jsonify({"status": "error", "message": str(e)})

@app.route("/api/orders/list")
@login_required
def api_orders_list():
    """List all purchase orders (draft + approved + received). Used by the
    purchase-orders page to show existing POs alongside any newly generated."""
    from models import PurchaseOrder, SKU
    qs = request.args.get("status", "").strip().lower()
    q = db.session.query(PurchaseOrder, SKU).join(SKU, PurchaseOrder.sku_id == SKU.id)
    if qs:
        q = q.filter(PurchaseOrder.po_status == qs)
    rows = q.order_by(PurchaseOrder.created_date.desc(), PurchaseOrder.po_number.desc()).all()
    # Group rows by po_number so the UI can render one card per PO
    grouped = {}
    for po, sku in rows:
        g = grouped.setdefault(po.po_number, {
            "po_number": po.po_number,
            "created_date": po.created_date.isoformat() if po.created_date else None,
            "supplier_name": po.supplier_name or "",
            "supplier_gstin": po.supplier_gstin or "",
            "po_status": po.po_status,
            "is_interstate": float(po.igst_rate or 0) > 0,
            "items": [],
            "subtotal": 0.0, "total_tax": 0.0, "grand_total": 0.0,
        })
        base = float(po.unit_price or 0) * int(po.qty_ordered or 0)
        cgst_amt = base * float(po.cgst_rate or 0) / 100
        sgst_amt = base * float(po.sgst_rate or 0) / 100
        igst_amt = base * float(po.igst_rate or 0) / 100
        item = {
            "sku_id": sku.sku_code, "product_name": sku.product_name,
            "hsn_code": sku.hsn_code or "", "qty": int(po.qty_ordered or 0),
            "unit_price": float(po.unit_price or 0), "base_amount": round(base, 2),
            "cgst_rate": float(po.cgst_rate or 0), "cgst_amount": round(cgst_amt, 2),
            "sgst_rate": float(po.sgst_rate or 0), "sgst_amount": round(sgst_amt, 2),
            "igst_rate": float(po.igst_rate or 0), "igst_amount": round(igst_amt, 2),
            "total": float(po.total_value or 0),
        }
        g["items"].append(item)
        g["subtotal"] += base
        g["total_tax"] += (igst_amt if g["is_interstate"] else cgst_amt + sgst_amt)
        g["grand_total"] += float(po.total_value or 0)
    out = []
    for v in grouped.values():
        v["subtotal"] = round(v["subtotal"], 2)
        v["total_tax"] = round(v["total_tax"], 2)
        v["grand_total"] = round(v["grand_total"], 2)
        v["item_count"] = len(v["items"])
        v["tax_type"] = "IGST" if v["is_interstate"] else "CGST+SGST"
        out.append(v)
    return jsonify(out)


@app.route("/api/orders/approved")
@login_required
def api_orders_approved():
    """List all approved purchase orders."""
    from models import PurchaseOrder, POStatus, SKU
    orders = db.session.query(PurchaseOrder, SKU).join(
        SKU, PurchaseOrder.sku_id == SKU.id
    ).filter(PurchaseOrder.po_status == POStatus.APPROVED).order_by(
        PurchaseOrder.created_date.desc()
    ).all()
    return jsonify([{
        "po_number": po.po_number,
        "sku_id": sku.sku_code,
        "product_name": sku.product_name,
        "brand": sku.brand,
        "category": sku.category,
        "qty_ordered": po.qty_ordered,
        "unit_price": float(po.unit_price or 0),
        "total_value": float(po.total_value or 0),
        "supplier_name": po.supplier_name or "",
        "created_date": po.created_date.isoformat() if po.created_date else "",
        "po_status": po.po_status,
        "lead_time": sku.supplier_lead_time_days or 7,
        "expected_date": (po.created_date + timedelta(days=sku.supplier_lead_time_days or 7)).isoformat() if po.created_date else ""
    } for po, sku in orders])

@app.route("/api/orders/in-transit")
@login_required
def api_orders_in_transit():
    """List all in-transit (approved but not received) orders with ETA."""
    from models import PurchaseOrder, POStatus, SKU
    orders = db.session.query(PurchaseOrder, SKU).join(
        SKU, PurchaseOrder.sku_id == SKU.id
    ).filter(PurchaseOrder.po_status.in_([POStatus.APPROVED])).order_by(
        PurchaseOrder.created_date.asc()
    ).all()
    today = datetime.utcnow().date()
    result = []
    for po, sku in orders:
        lead_days = sku.supplier_lead_time_days or 7
        expected = po.created_date + timedelta(days=lead_days) if po.created_date else today
        days_remaining = (expected - today).days
        progress = max(0, min(100, int((1 - days_remaining / max(lead_days, 1)) * 100)))
        status = "delayed" if days_remaining < 0 else "arriving_soon" if days_remaining <= 2 else "on_track"
        result.append({
            "po_number": po.po_number,
            "sku_id": sku.sku_code,
            "product_name": sku.product_name,
            "category": sku.category,
            "qty_ordered": po.qty_ordered,
            "supplier_name": po.supplier_name or "",
            "order_date": po.created_date.isoformat() if po.created_date else "",
            "expected_date": expected.isoformat(),
            "days_remaining": days_remaining,
            "progress_pct": progress,
            "status": status
        })
    return jsonify(result)

@app.route("/api/po/<po_number>/approve", methods=["POST"])
@login_required
@role_required("owner")
def api_po_approve(po_number):
    """Move a draft PO to APPROVED so it surfaces in /reorder Approved Orders tab."""
    from models import PurchaseOrder, POStatus
    rows = PurchaseOrder.query.filter_by(po_number=po_number).all()
    if not rows:
        return jsonify({"status": "error", "message": "PO not found"}), 404
    for po in rows:
        po.po_status = POStatus.APPROVED
    db.session.commit()
    return jsonify({"status": "success", "message": f"{po_number} approved ({len(rows)} line items)"})


@app.route("/api/po/<po_number>/pdf")
@login_required
def api_po_pdf(po_number):
    """Brief Part 6A: download a PO as a GST-compliant PDF invoice.
    Aggregates all DB rows sharing the same po_number (one row per item),
    determines interstate/intrastate, and renders via po_pdf.render_po_pdf."""
    from models import PurchaseOrder, SKU, Store
    from auth import get_user_store_ids
    rows = db.session.query(PurchaseOrder, SKU).join(
        SKU, PurchaseOrder.sku_id == SKU.id
    ).filter(PurchaseOrder.po_number == po_number).all()
    if not rows:
        return "PO not found", 404
    store_ids = get_user_store_ids() or []
    first_po = rows[0][0]
    if store_ids and first_po.store_id not in store_ids:
        return "Forbidden", 403
    store = Store.query.get(first_po.store_id)
    is_interstate = float(first_po.igst_rate or 0) > 0
    items = []
    for po, sku in rows:
        rate = float(po.cgst_rate or 0) * 2 if not is_interstate else float(po.igst_rate or 0)
        base = float(po.unit_price or 0) * int(po.qty_ordered or 0)
        cgst_amt = base * float(po.cgst_rate or 0) / 100
        sgst_amt = base * float(po.sgst_rate or 0) / 100
        igst_amt = base * float(po.igst_rate or 0) / 100
        items.append({
            "sku_id": sku.sku_code, "product_name": sku.product_name,
            "hsn_code": sku.hsn_code or "—",
            "qty": int(po.qty_ordered or 0),
            "unit_price": float(po.unit_price or 0),
            "base_amount": round(base, 2),
            "gst_rate": rate,
            "cgst_rate": float(po.cgst_rate or 0),
            "cgst_amount": round(cgst_amt, 2),
            "sgst_rate": float(po.sgst_rate or 0),
            "sgst_amount": round(sgst_amt, 2),
            "igst_rate": float(po.igst_rate or 0),
            "igst_amount": round(igst_amt, 2),
            "total": float(po.total_value or 0),
        })
    supplier = {
        "name": first_po.supplier_name or "—",
        "gstin": first_po.supplier_gstin or "—",
        "state": (rows[0][1].supplier_state if rows[0][1] else "—") or "—",
    }
    store_meta = {
        "name": store.name if store else "—",
        "city": store.city if store else "—",
        "state": (store.state if store else "—") or "—",
        "gstin": (store.gstin if store else "—") or "—",
    }
    from po_pdf import render_po_pdf
    pdf_io = render_po_pdf(po_number, items, store_meta, supplier, is_interstate)
    from flask import send_file as _send_file
    return _send_file(pdf_io, mimetype="application/pdf",
                      as_attachment=True, download_name=f"{po_number}.pdf")


@app.route("/api/orders/receive", methods=["POST"])
@login_required
@role_required("owner", "manager")
def api_orders_receive():
    """Mark an order as received."""
    from models import PurchaseOrder, POStatus
    po_number = request.json.get("po_number", "")
    po = PurchaseOrder.query.filter_by(po_number=po_number).first()
    if not po:
        return jsonify({"status": "error", "message": "PO not found"})
    po.po_status = POStatus.RECEIVED
    db.session.commit()
    return jsonify({"status": "success", "message": f"{po_number} marked as received"})

# ── SKU Update API ──

@app.route("/api/sku/update", methods=["POST"])
@login_required
@role_required("owner", "manager")
def api_sku_update():
    """Update an existing SKU's details."""
    try:
        from models import SKU
        data = request.json
        sku_code = data.get("sku_id", "").strip()
        if not sku_code:
            return jsonify({"status": "error", "message": "SKU code required"})
        sku = SKU.query.filter_by(sku_code=sku_code).first()
        if not sku:
            return jsonify({"status": "error", "message": f"{sku_code} not found"})
        if "product_name" in data: sku.product_name = data["product_name"]
        if "brand" in data: sku.brand = data["brand"]
        if "category" in data: sku.category = data["category"]
        if "unit_price" in data: sku.unit_price = float(data["unit_price"])
        if "cost_price" in data: sku.cost_price = float(data["cost_price"])
        if "shelf_life_days" in data: sku.shelf_life_days = int(data["shelf_life_days"])
        if "moq_from_supplier" in data: sku.moq_from_supplier = int(data["moq_from_supplier"])
        if "supplier_lead_time_days" in data: sku.supplier_lead_time_days = int(data["supplier_lead_time_days"])
        if "hsn_code" in data: sku.hsn_code = (data["hsn_code"] or None)
        if "gst_rate" in data: sku.gst_rate = float(data["gst_rate"]) if data["gst_rate"] not in (None, "") else None
        if "supplier_name" in data: sku.supplier_name = (data["supplier_name"] or None)
        if "supplier_gstin" in data: sku.supplier_gstin = (data["supplier_gstin"] or None)
        if "supplier_state" in data: sku.supplier_state = (data["supplier_state"] or None)
        db.session.commit()
        return jsonify({"status": "success", "message": f"{sku_code} updated successfully"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"status": "error", "message": str(e)})

def _lan_ip():
    """Best-effort: return the host's LAN IP. From inside Docker this
    typically returns the container's bridge IP (useless for a phone on
    the host LAN); set LEDGR_PUBLIC_HOST in that case."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 53))
        ip = s.getsockname()[0]
        s.close()
        # Skip docker bridge addresses — they're not reachable from the phone.
        if ip.startswith(("172.16.", "172.17.", "172.18.", "172.19.", "172.20.",
                         "172.21.", "172.22.", "172.23.", "172.24.", "172.25.",
                         "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.",
                         "10.0.0.", "127.")):
            return None
        return ip
    except Exception:
        return None


@app.route("/api/qr-pairing")
@login_required
def api_qr_pairing():
    """Returns the pairing JSON the Android scanner reads from a QR code.
    Resolution order for the server URL:
      1. LEDGR_PUBLIC_HOST env var (e.g. 192.168.1.10 or 192.168.1.10:5000)
      2. Request's Host header — works when the user opens the dashboard
         via the LAN IP directly (http://192.168.1.10:5000/...)
      3. socket-derived LAN IP (works outside Docker; skipped for bridge IPs)
      4. Last resort: whatever the request says (likely localhost — phone
         won't be able to reach this).
    A phone on the same Wi-Fi cannot reach the laptop's localhost, so the
    QR has to encode a routable address."""
    from models import Store
    from urllib.parse import urlparse, urlunparse

    requested_base = request.host_url.rstrip("/")
    parsed = urlparse(requested_base)
    host_only = (parsed.hostname or "").lower()
    port = parsed.port

    # 1) Explicit env override (recommended for Docker)
    public_host = (os.environ.get("LEDGR_PUBLIC_HOST") or "").strip()

    if public_host:
        # Allow either "192.168.x.y" or "192.168.x.y:5000"
        if ":" in public_host:
            netloc = public_host
        else:
            netloc = f"{public_host}:{port or 5000}"
        base = urlunparse((parsed.scheme, netloc, "", "", "", ""))
    elif host_only not in ("localhost", "127.0.0.1", "0.0.0.0", ""):
        # 2) User accessed via LAN IP — use what they typed.
        base = requested_base
    else:
        # 3) Try to detect (works outside Docker only).
        lan = _lan_ip()
        base = (urlunparse((parsed.scheme, f"{lan}:{port or 5000}", "", "", "", ""))
                if lan else requested_base)

    store = Store.query.first()
    return jsonify({
        "server_url": base,
        "name": (store.name if store else "Ledgr") + " · Ledgr",
        "issued_at": datetime.utcnow().isoformat() + "Z",
    })


@app.route("/mobile/service-worker.js")
def mobile_service_worker():
    """Brief Part 6B: service worker file is served unauthenticated so the
    browser can register it. The file itself contains no secrets — it just
    caches the shell URL paths and forwards everything else to fetch()."""
    sw_path = os.path.join(ROOT, "mobile", "service-worker.js")
    if not os.path.exists(sw_path):
        return "Not found", 404
    resp = send_file(sw_path, mimetype="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/mobile/"
    return resp

@app.route("/mobile/manifest.json")
def mobile_manifest():
    """Manifest is served unauthenticated so browsers honour the install prompt
    even before login."""
    p = os.path.join(ROOT, "mobile", "manifest.json")
    if not os.path.exists(p):
        return "Not found", 404
    return send_file(p, mimetype="application/manifest+json")

@app.route("/mobile/")
@app.route("/mobile/<path:filename>")
@login_required
def mobile_pwa(filename="index.html"):
    """Serve the barcode scanner PWA. Login-gated; PWA shell paths require
    a valid session (PWA re-auths via /login flow)."""
    mobile_dir = os.path.join(ROOT, "mobile")
    full = os.path.normpath(os.path.join(mobile_dir, filename))
    if not full.startswith(mobile_dir):
        return "Forbidden", 403
    if not os.path.exists(full):
        return "Not found", 404
    return send_file(full)

@app.route("/api/sku/scan", methods=["POST"])
@login_required
def api_sku_scan():
    """Phase 10 fix: Barcode scan writes to DB, not JSON file. Salesman-scoped
    to their assigned stores so a Nashik scanner can't write Pune batches."""
    try:
        from database import log_barcode_scan
        from auth import get_user_store_ids
        ok, msg = log_barcode_scan(request.json, store_ids=get_user_store_ids())
        return jsonify({"status": "success" if ok else "error", "message": msg})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

# ── Inventory Audit Trail (Brief Phase 14) ──
@app.route("/audit-trail")
@login_required
def page_audit_trail():
    """Phase 6: Inventory Audit Trail."""
    return render_template("audit_trail.html", page="audit_trail")

@app.route("/settings")
@login_required
def page_settings():
    """Application Settings & Profile."""
    return render_template("settings.html", page="settings")

@app.route("/api/audit-trail")
@login_required
def api_audit_trail():
    """Phase 14 fix: Audit trail from DB, not JSON file."""
    from database import get_audit_trail
    from auth import get_user_store_ids
    return jsonify(get_audit_trail(store_ids=get_user_store_ids()))

@app.route("/api/audit-trail/add", methods=["POST"])
@login_required
@role_required("owner", "manager")
def api_audit_add():
    """Phase 14 fix: Record adjustment to DB, not JSON file."""
    try:
        from database import add_audit_entry
        from auth import get_user_store_ids
        data = request.json
        user_name = current_user.full_name if current_user.is_authenticated else "System"
        stores = get_user_store_ids() or []
        store_id = stores[0] if stores else None
        if not store_id:
            return jsonify({"status": "error", "message": "User has no store assignment"}), 403
        add_audit_entry(user_name, data.get("sku_id",""), data.get("field","warehouse_stock"),
                        data.get("old_value",0), data.get("new_value",0), data.get("reason",""),
                        store_id=store_id)
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

# ── Context processor: inject globals into all templates (Phase 14) ──
@app.context_processor
def inject_globals():
    ai_mode = "openrouter" if OPENROUTER_KEY else "local"
    sku_count, outlet_count = _live_counts()
    show_demo_creds = os.environ.get("HIDE_DEMO_CREDENTIALS", "").lower() not in ("1", "true", "yes")
    return {
        "ai_mode": ai_mode,
        "app_version": "2.0.0",
        "live_sku_count": sku_count,
        "live_outlet_count": outlet_count,
        "show_demo_credentials": show_demo_creds,
    }

os.makedirs("logs", exist_ok=True)
os.makedirs(PROCESSED, exist_ok=True)
os.makedirs(DATA, exist_ok=True)

# First-boot pipeline auto-run: writes monday_report.json + companion files
# to data/processed/ if they aren't there yet. ensure_pipeline() is a no-op
# if outputs already exist, so this is safe to run on every import. With
# gunicorn --preload it fires once in the master process, not per-worker.
# The Dockerfile sets --timeout 120 to cover the ~45-60s first-boot pipeline.
if os.environ.get("LEDGR_SKIP_AUTO_PIPELINE", "").lower() not in ("1", "true", "yes"):
    try:
        print("[boot] Checking if pipeline needs to run...")
        ensure_pipeline()
        print("[boot] Pipeline check complete")
    except Exception as _e:
        print(f"[boot] ensure_pipeline failed (non-fatal): {_e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
