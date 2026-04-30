"""
app.py — Flask web application with Tabler UI dashboard
Serves API endpoints and HTML pages for the demand forecasting system.
"""
import os, sys, json, threading
import pandas as pd
from flask import Flask, render_template, jsonify, request, send_file

app = Flask(__name__, template_folder="templates", static_folder="static")
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

# ── Pages ──
@app.route("/")
def index():
    return render_template("overview.html", page="overview")

@app.route("/retrospective")
def retrospective():
    return render_template("retrospective.html", page="retrospective")

@app.route("/forecast")
def forecast():
    return render_template("forecast.html", page="forecast")

@app.route("/reorder")
def reorder():
    return render_template("reorder.html", page="reorder")

@app.route("/classification")
def classification():
    return render_template("classification.html", page="classification")

@app.route("/accuracy")
def accuracy():
    return render_template("accuracy.html", page="accuracy")

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

@app.route("/api/run-pipeline", methods=["POST"])
def api_run_pipeline():
    try:
        sys.path.insert(0, ROOT)
        from pipeline import run_pipeline
        results = run_pipeline()
        return jsonify({"status": "success", "results": results})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/download-reorder")
def download_reorder():
    path = os.path.join(PROCESSED, "reorder_recommendations.csv")
    if os.path.exists(path):
        return send_file(path, as_attachment=True, download_name="reorder_plan.csv")
    return "File not found", 404

if __name__ == "__main__":
    ensure_pipeline()
    app.run(debug=True, host="0.0.0.0", port=5000)
