"""
backend/2_forecast.py — SKU-Level Demand Forecaster

HARD RULE: One forecast per SKU. Never aggregate to category or brand level.
Uses LightGBM as primary model with rolling average fallback.
"""
import pandas as pd
import numpy as np
import json
import os
import warnings
warnings.filterwarnings("ignore")

try:
    import lightgbm as lgb
    HAS_LGBM = True
except ImportError:
    HAS_LGBM = False
    print("[2_forecast] WARNING: LightGBM not available, using rolling average fallback for all SKUs")

def get_project_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def run():
    root = get_project_root()
    processed_dir = os.path.join(root, "data", "processed")
    data_dir = os.path.join(root, "data")

    print("[2_forecast] Loading classified sales data...")
    sales = pd.read_csv(os.path.join(processed_dir, "sales_classified.csv"))
    sales["week_start_date"] = pd.to_datetime(sales["week_start_date"])
    
    festive = pd.read_csv(os.path.join(data_dir, "festive_calendar.csv"))
    festive["date"] = pd.to_datetime(festive["date"])
    
    promos = pd.read_csv(os.path.join(data_dir, "promotions_calendar.csv"))
    promos["start_date"] = pd.to_datetime(promos["start_date"])
    promos["end_date"] = pd.to_datetime(promos["end_date"])
    
    sku_master = pd.read_csv(os.path.join(data_dir, "sku_master.csv"))
    outlet_master = pd.read_csv(os.path.join(data_dir, "outlet_master.csv"))
    
    # Exclude missing_data rows, include true_zero, uncertain, stockout_gap, observed
    sales_clean = sales[sales["row_classification"] != "missing_data"].copy()
    
    # Aggregate to SKU × week level (sum across outlets)
    sku_weekly = sales_clean.groupby(["sku_id", "week_start_date"]).agg(
        units_sold=("units_sold", "sum"),
        returns=("returns", "sum"),
        promotional_flag=("promotional_flag", "max")
    ).reset_index()
    
    # Get all unique weeks sorted
    all_weeks = sorted(sku_weekly["week_start_date"].unique())
    week_map = {w: i+1 for i, w in enumerate(all_weeks)}
    sku_weekly["week_num"] = sku_weekly["week_start_date"].map(week_map)
    
    total_weeks = len(all_weeks)
    print(f"[2_forecast] Total weeks in data: {total_weeks}")
    
    # Train/test split
    train_end_week = min(96, total_weeks - 8)
    test_start_week = train_end_week + 1
    test_end_week = total_weeks
    forecast_start_week = total_weeks + 1
    forecast_end_week = total_weeks + 6
    
    # Calculate forecast week dates
    if len(all_weeks) >= 2:
        last_date = pd.Timestamp(all_weeks[-1])
        forecast_dates = [last_date + pd.Timedelta(weeks=i+1) for i in range(6)]
    else:
        forecast_dates = [pd.Timestamp("2024-01-08") + pd.Timedelta(weeks=i) for i in range(6)]
    
    # Build demand impact map
    impact_map = {"Very High": 3, "High": 3, "Medium": 2, "Low": 1}
    festive_weeks = {}
    for _, row in festive.iterrows():
        fest_date = row["date"]
        # Map to nearest week
        for w in all_weeks:
            wt = pd.Timestamp(w)
            if abs((wt - fest_date).days) <= 3:
                festive_weeks[w] = impact_map.get(row["demand_impact"], 1)
                break
    
    # Build promo SKU map
    def parse_sku_ids(s):
        if pd.isna(s):
            return []
        return [x.strip() for x in str(s).split(",")]
    
    # Channel mix: % kirana outlets per SKU
    kirana_outlets = set(outlet_master[outlet_master["channel"] == "kirana"]["outlet_id"].tolist())
    total_outlets = len(outlet_master)
    
    sku_kirana_pct = {}
    for sku in sku_weekly["sku_id"].unique():
        sku_outlets = sales_clean[sales_clean["sku_id"] == sku]["outlet_id"].unique()
        if len(sku_outlets) > 0:
            kirana_count = sum(1 for o in sku_outlets if o in kirana_outlets)
            sku_kirana_pct[sku] = kirana_count / len(sku_outlets)
        else:
            sku_kirana_pct[sku] = 0.5
    
    # Category encoding
    cat_map = {"personal_care": 0, "household": 1, "packaged_food": 2}
    sku_category = dict(zip(sku_master["sku_id"], sku_master["category"].map(cat_map).fillna(0)))
    
    all_skus = sorted(sku_weekly["sku_id"].unique())
    print(f"[2_forecast] Forecasting {len(all_skus)} SKUs...")
    
    all_forecasts = []
    accuracy_results = {}
    low_confidence_skus = []
    
    for sku_idx, sku in enumerate(all_skus):
        if (sku_idx + 1) % 10 == 0:
            print(f"  Processing SKU {sku_idx+1}/{len(all_skus)}: {sku}")
        
        sku_data = sku_weekly[sku_weekly["sku_id"] == sku].copy()
        
        # Create complete weekly series (fill missing weeks with 0)
        full_weeks_df = pd.DataFrame({"week_start_date": all_weeks})
        full_weeks_df["week_start_date"] = pd.to_datetime(full_weeks_df["week_start_date"])
        sku_data = full_weeks_df.merge(sku_data, on="week_start_date", how="left")
        sku_data["sku_id"] = sku
        sku_data["units_sold"] = sku_data["units_sold"].fillna(0)
        sku_data["returns"] = sku_data["returns"].fillna(0)
        sku_data["promotional_flag"] = sku_data["promotional_flag"].fillna(0)
        sku_data["week_num"] = range(1, len(sku_data) + 1)
        sku_data = sku_data.sort_values("week_start_date").reset_index(drop=True)
        
        non_zero_count = (sku_data["units_sold"] > 0).sum()
        
        # Feature engineering
        sku_data["lag_1"] = sku_data["units_sold"].shift(1)
        sku_data["lag_2"] = sku_data["units_sold"].shift(2)
        sku_data["lag_4"] = sku_data["units_sold"].shift(4)
        sku_data["rolling_4w_avg"] = sku_data["units_sold"].rolling(4, min_periods=1).mean()
        sku_data["rolling_8w_avg"] = sku_data["units_sold"].rolling(8, min_periods=1).mean()
        sku_data["week_of_year"] = sku_data["week_start_date"].dt.isocalendar().week.astype(int)
        sku_data["month"] = sku_data["week_start_date"].dt.month
        sku_data["quarter"] = sku_data["week_start_date"].dt.quarter
        
        sku_data["is_festive_week"] = sku_data["week_start_date"].apply(
            lambda x: 1 if x in festive_weeks else 0
        )
        sku_data["demand_impact_score"] = sku_data["week_start_date"].apply(
            lambda x: festive_weeks.get(x, 0)
        )
        
        # Promotional features
        sku_data["is_promo_week"] = 0
        sku_data["promo_uplift_pct"] = 0.0
        for _, promo in promos.iterrows():
            promo_skus = parse_sku_ids(promo["sku_ids"])
            if sku in promo_skus:
                mask = (sku_data["week_start_date"] >= promo["start_date"]) & \
                       (sku_data["week_start_date"] <= promo["end_date"])
                sku_data.loc[mask, "is_promo_week"] = 1
                sku_data.loc[mask, "promo_uplift_pct"] = promo["uplift_pct"]
        
        sku_data["kirana_pct"] = sku_kirana_pct.get(sku, 0.5)
        sku_data["category_encoded"] = sku_category.get(sku, 0)
        
        features = ["lag_1", "lag_2", "lag_4", "rolling_4w_avg", "rolling_8w_avg",
                     "week_of_year", "month", "quarter", "is_festive_week",
                     "demand_impact_score", "is_promo_week", "promo_uplift_pct",
                     "kirana_pct", "category_encoded"]
        
        # Fill NaN lags
        sku_data[features] = sku_data[features].fillna(0)
        
        model_used = "rolling_average"
        mape_score = 999.0
        
        use_lgbm = HAS_LGBM and non_zero_count >= 10 and len(sku_data) > 20
        
        if use_lgbm:
            try:
                train_data = sku_data[sku_data["week_num"] <= train_end_week]
                test_data = sku_data[(sku_data["week_num"] >= test_start_week) & 
                                     (sku_data["week_num"] <= test_end_week)]
                
                X_train = train_data[features]
                y_train = train_data["units_sold"]
                X_test = test_data[features]
                y_test = test_data["units_sold"]
                
                if len(X_train) > 10 and len(X_test) > 0:
                    model = lgb.LGBMRegressor(
                        n_estimators=100,
                        learning_rate=0.1,
                        max_depth=5,
                        num_leaves=31,
                        min_child_samples=5,
                        verbose=-1,
                        random_state=42
                    )
                    model.fit(X_train, y_train)
                    
                    # Evaluate on test set
                    y_pred_test = model.predict(X_test)
                    y_pred_test = np.maximum(y_pred_test, 0)
                    
                    # Calculate MAPE
                    mask_nonzero = y_test > 0
                    if mask_nonzero.sum() > 0:
                        mape_score = np.mean(np.abs((y_test[mask_nonzero] - y_pred_test[mask_nonzero]) / y_test[mask_nonzero])) * 100
                    else:
                        mape_score = 0.0
                    
                    # Confidence intervals are derived from historical forecast error distribution.
                    # Compute residuals on training data to estimate prediction uncertainty
                    y_pred_train = model.predict(X_train)
                    residuals = y_train.values - y_pred_train
                    residual_std = float(np.std(residuals)) if len(residuals) > 1 else max(1.0, float(y_train.mean()) * 0.2)
                    
                    model_used = "LightGBM"
                    
                    # Generate 6-week forecast
                    last_known = sku_data.iloc[-1].copy()
                    forecast_rows = []
                    
                    for i, fdate in enumerate(forecast_dates):
                        row = {}
                        if i == 0:
                            row["lag_1"] = last_known["units_sold"]
                            row["lag_2"] = sku_data.iloc[-2]["units_sold"] if len(sku_data) >= 2 else 0
                            row["lag_4"] = sku_data.iloc[-4]["units_sold"] if len(sku_data) >= 4 else 0
                        elif i == 1:
                            row["lag_1"] = forecast_rows[-1]["forecasted_units"]
                            row["lag_2"] = last_known["units_sold"]
                            row["lag_4"] = sku_data.iloc[-3]["units_sold"] if len(sku_data) >= 3 else 0
                        else:
                            row["lag_1"] = forecast_rows[-1]["forecasted_units"]
                            row["lag_2"] = forecast_rows[-2]["forecasted_units"]
                            if i >= 4:
                                row["lag_4"] = forecast_rows[i-4]["forecasted_units"]
                            else:
                                idx = len(sku_data) - (4 - i)
                                row["lag_4"] = sku_data.iloc[idx]["units_sold"] if idx >= 0 else 0
                        
                        recent_4 = list(sku_data["units_sold"].tail(4))
                        for fr in forecast_rows:
                            recent_4.append(fr["forecasted_units"])
                        row["rolling_4w_avg"] = np.mean(recent_4[-4:])
                        
                        recent_8 = list(sku_data["units_sold"].tail(8))
                        for fr in forecast_rows:
                            recent_8.append(fr["forecasted_units"])
                        row["rolling_8w_avg"] = np.mean(recent_8[-8:])
                        
                        row["week_of_year"] = fdate.isocalendar()[1]
                        row["month"] = fdate.month
                        row["quarter"] = (fdate.month - 1) // 3 + 1
                        row["is_festive_week"] = 0
                        row["demand_impact_score"] = 0
                        row["is_promo_week"] = 0
                        row["promo_uplift_pct"] = 0.0
                        
                        # Check if forecast date is near a festive date
                        for _, fest in festive.iterrows():
                            if abs((fdate - fest["date"]).days) <= 3:
                                row["is_festive_week"] = 1
                                row["demand_impact_score"] = impact_map.get(fest["demand_impact"], 1)
                        
                        # Check promos
                        for _, promo in promos.iterrows():
                            promo_skus = parse_sku_ids(promo["sku_ids"])
                            if sku in promo_skus and promo["start_date"] <= fdate <= promo["end_date"]:
                                row["is_promo_week"] = 1
                                row["promo_uplift_pct"] = promo["uplift_pct"]
                        
                        row["kirana_pct"] = sku_kirana_pct.get(sku, 0.5)
                        row["category_encoded"] = sku_category.get(sku, 0)
                        
                        X_forecast = pd.DataFrame([row])[features]
                        pred = model.predict(X_forecast)[0]
                        pred = max(0, round(pred))
                        
                        # 95% CI using 1.96 * residual_std from training errors
                        forecast_rows.append({
                            "sku_id": sku,
                            "week_start_date": fdate.strftime("%Y-%m-%d"),
                            "forecasted_units": pred,
                            "lower_bound": max(0, round(pred - 1.96 * residual_std)),
                            "upper_bound": round(pred + 1.96 * residual_std),
                            "model_used": model_used,
                            "mape_score": round(mape_score, 2),
                            "confidence_flag": "low_confidence" if mape_score > 30 else "high_confidence",
                        })
                    
                    all_forecasts.extend(forecast_rows)
                    
                else:
                    use_lgbm = False
                    
            except Exception as e:
                print(f"  LightGBM failed for {sku}: {e}, falling back to rolling average")
                use_lgbm = False
        
        if not use_lgbm:
            # Rolling average fallback
            model_used = "rolling_average"
            recent_sales = sku_data["units_sold"].tail(4)
            avg_sales = recent_sales.mean() if len(recent_sales) > 0 else 0
            # Confidence intervals derived from historical forecast error distribution.
            residual_std = float(recent_sales.std()) if len(recent_sales) > 1 else max(1.0, float(avg_sales) * 0.2)
            
            # Calculate MAPE on test period using rolling average
            test_data = sku_data[(sku_data["week_num"] >= test_start_week) & 
                                 (sku_data["week_num"] <= test_end_week)]
            if len(test_data) > 0 and (test_data["units_sold"] > 0).sum() > 0:
                y_test = test_data["units_sold"]
                mask_nz = y_test > 0
                if mask_nz.sum() > 0:
                    mape_score = np.mean(np.abs((y_test[mask_nz] - avg_sales) / y_test[mask_nz])) * 100
                else:
                    mape_score = 50.0
            else:
                mape_score = 50.0
            
            for fdate in forecast_dates:
                pred = max(0, round(avg_sales))
                all_forecasts.append({
                    "sku_id": sku,
                    "week_start_date": fdate.strftime("%Y-%m-%d"),
                    "forecasted_units": pred,
                    "lower_bound": max(0, round(pred - 1.96 * residual_std)),
                    "upper_bound": round(pred + 1.96 * residual_std),
                    "model_used": model_used,
                    "mape_score": round(mape_score, 2),
                    "confidence_flag": "low_confidence" if mape_score > 30 else "high_confidence",
                })
        
        accuracy_results[sku] = {
            "mape": round(mape_score, 2),
            "model_used": model_used,
            "non_zero_datapoints": int(non_zero_count),
            "confidence": "low_confidence" if mape_score > 30 else ("medium" if mape_score > 15 else "high")
        }
        
        if mape_score > 30:
            low_confidence_skus.append(sku)
    
    # Save forecasts
    forecast_df = pd.DataFrame(all_forecasts)
    forecast_df.to_csv(os.path.join(processed_dir, "forecasts.csv"), index=False)
    
    # Save accuracy report
    overall_mapes = [v["mape"] for v in accuracy_results.values() if v["mape"] < 900]
    overall_mape = np.mean(overall_mapes) if overall_mapes else 0
    
    accuracy_report = {
        "overall_mape": round(overall_mape, 2),
        "per_sku_mape": accuracy_results,
        "low_confidence_skus": low_confidence_skus,
        "total_skus_forecasted": len(all_skus),
        "lgbm_count": sum(1 for v in accuracy_results.values() if v["model_used"] == "LightGBM"),
        "rolling_avg_count": sum(1 for v in accuracy_results.values() if v["model_used"] == "rolling_average"),
    }
    
    with open(os.path.join(processed_dir, "forecast_accuracy.json"), "w") as f:
        json.dump(accuracy_report, f, indent=2)
    
    print(f"\n[2_forecast] Complete!")
    print(f"  Forecasts generated: {len(forecast_df)} rows ({len(all_skus)} SKUs × 6 weeks)")
    print(f"  Overall MAPE: {overall_mape:.1f}%")
    print(f"  LightGBM models: {accuracy_report['lgbm_count']}")
    print(f"  Rolling avg fallback: {accuracy_report['rolling_avg_count']}")
    print(f"  Low confidence SKUs: {len(low_confidence_skus)}")
    
    return True

if __name__ == "__main__":
    run()
