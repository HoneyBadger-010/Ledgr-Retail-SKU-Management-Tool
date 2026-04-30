"""
backend/5_sku_classifier.py — SKU Classification + ABC Analysis
"""
import pandas as pd, numpy as np, os

def get_project_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def run():
    root = get_project_root()
    data_dir = os.path.join(root, "data")
    processed_dir = os.path.join(root, "data", "processed")
    print("[5_sku_classifier] Loading data...")
    sales = pd.read_csv(os.path.join(processed_dir, "sales_classified.csv"))
    sales["week_start_date"] = pd.to_datetime(sales["week_start_date"])
    sku_master = pd.read_csv(os.path.join(data_dir, "sku_master.csv"))
    festive = pd.read_csv(os.path.join(data_dir, "festive_calendar.csv"))
    festive["date"] = pd.to_datetime(festive["date"])
    clean = sales[sales["row_classification"]!="missing_data"]
    sku_weekly = clean.groupby(["sku_id","week_start_date"]).agg(units_sold=("units_sold","sum")).reset_index()
    all_weeks = sorted(sku_weekly["week_start_date"].unique())
    total_weeks = len(all_weeks)
    max_date = sku_weekly["week_start_date"].max()
    last8w_start = max_date - pd.Timedelta(weeks=8)
    # Festive weeks
    festive_dates = set()
    for _,f in festive.iterrows():
        for w in all_weeks:
            if abs((pd.Timestamp(w)-f["date"]).days)<=3:
                festive_dates.add(w); break
    results = []
    for sku in sorted(sku_master["sku_id"].unique()):
        sd = sku_weekly[sku_weekly["sku_id"]==sku]
        ski = sku_master[sku_master["sku_id"]==sku].iloc[0]
        avg_ws = sd["units_sold"].mean() if len(sd)>0 else 0
        weeks_present = sd[sd["units_sold"]>0]["week_start_date"].nunique()
        presence_pct = weeks_present/total_weeks if total_weeks>0 else 0
        cv = sd["units_sold"].std()/avg_ws if avg_ws>0 else 0
        # Festive vs normal
        fest_sales = sd[sd["week_start_date"].isin(festive_dates)]["units_sold"].mean() if len(sd[sd["week_start_date"].isin(festive_dates)])>0 else 0
        norm_sales = sd[~sd["week_start_date"].isin(festive_dates)]["units_sold"].mean() if len(sd[~sd["week_start_date"].isin(festive_dates)])>0 else avg_ws
        fest_sensitivity = fest_sales/norm_sales if norm_sales>0 else 0
        # Last 8 weeks
        l8 = sd[sd["week_start_date"]>=last8w_start]["units_sold"].sum()
        # Revenue
        total_revenue = sd["units_sold"].sum() * ski["unit_price"]
        results.append({"sku_id":sku,"product_name":ski["product_name"],"brand":ski["brand"],
            "category":ski["category"],"avg_weekly_sales":round(avg_ws,1),"weeks_present":weeks_present,
            "presence_pct":round(presence_pct,2),"cv_score":round(cv,2),
            "festive_sensitivity_score":round(fest_sensitivity,2),"last_8w_sales":round(l8),
            "total_revenue":round(total_revenue)})
    df = pd.DataFrame(results)
    # Movement classification
    p75 = df["avg_weekly_sales"].quantile(0.75)
    p25 = df["avg_weekly_sales"].quantile(0.25)
    def classify(r):
        if r["last_8w_sales"]==0: return "dead_stock"
        if r["cv_score"]>0.6 and r["festive_sensitivity_score"]>2: return "seasonal"
        if r["avg_weekly_sales"]>p75 and r["presence_pct"]>0.7: return "fast_mover"
        if r["avg_weekly_sales"]<p25 or r["presence_pct"]<0.3: return "slow_mover"
        return "fast_mover"  # default to fast if doesn't fit other categories
    df["movement_class"] = df.apply(classify, axis=1)
    # ABC analysis
    df = df.sort_values("total_revenue",ascending=False)
    df["cum_revenue_pct"] = df["total_revenue"].cumsum()/df["total_revenue"].sum()*100
    def abc(pct):
        if pct<=70: return "A"
        if pct<=90: return "B"
        return "C"
    df["abc_class"] = df["cum_revenue_pct"].apply(abc)
    df["sales_velocity_rank"] = df["avg_weekly_sales"].rank(ascending=False,method="min").astype(int)
    df = df.drop(columns=["cum_revenue_pct"])
    df.to_csv(os.path.join(processed_dir,"sku_classification.csv"),index=False)
    print(f"[5_sku_classifier] Complete!")
    for mc in ["fast_mover","slow_mover","seasonal","dead_stock"]:
        print(f"  {mc}: {(df['movement_class']==mc).sum()} SKUs")
    for ac in ["A","B","C"]:
        cnt = (df["abc_class"]==ac).sum()
        rev = df[df["abc_class"]==ac]["total_revenue"].sum()
        print(f"  {ac}-class: {cnt} SKUs, Rs.{rev:,.0f} revenue")
    return True

if __name__ == "__main__":
    run()
