# 🌅 Sunrise Demand AI — Intelligent Inventory Optimization System

> **AI-powered demand forecasting and inventory optimization for FMCG distributors**
> Built for Sunrise Consumer Goods — Pune & Nashik distribution network

![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.0-000000?style=flat-square&logo=flask)
![LightGBM](https://img.shields.io/badge/LightGBM-ML-green?style=flat-square)
![Tabler](https://img.shields.io/badge/Tabler-UI-206bc4?style=flat-square)
![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)

---

## 📋 Problem Statement

**PS 5 — The Demand Mirage**: FMCG distributors face a critical data quality challenge — when sales show zero, does it mean genuine lack of demand, or is data simply missing? Getting this wrong leads to either devastating stockouts during peak seasons (like Diwali) or costly overstock situations.

This system solves the **True Zero vs Missing Data** classification problem and builds an end-to-end intelligent inventory optimization pipeline.

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Flask Web Server                      │
│              (Tabler UI Dashboard)                       │
├─────────────┬──────────┬──────────┬─────────────────────┤
│  Overview   │ Forecast │ Reorder  │ Classification      │
│  Dashboard  │ Explorer │ Plan     │ & Accuracy          │
└─────┬───────┴────┬─────┴────┬─────┴─────────────────────┘
      │            │          │
┌─────▼────────────▼──────────▼───────────────────────────┐
│              6-Step Backend Pipeline                      │
│                                                          │
│  1. Data Classification (True Zero / Missing Data)       │
│  2. LightGBM Demand Forecasting (SKU-level, 6-week)     │
│  3. Diwali 2023 Retrospective (No-lookahead detection)  │
│  4. Reorder Engine (MOQ / Shelf-life / Safety Stock)    │
│  5. SKU Classification (Fast/Slow/Seasonal/Dead + ABC)  │
│  6. Monday Morning Report Generator                      │
└──────────────────────────────────────────────────────────┘
```

---

## ✨ Key Features

### 1. True Zero vs Missing Data Classification
- **Full grid reconstruction**: Expands sparse sales data into complete `week × SKU × outlet` matrix (1.99M rows from 93.6K observed)
- **3-step rule-based classifier**: Outlet reporting check → Stockout gap detection → Channel frequency baseline
- **5 classifications**: `observed`, `true_zero`, `missing_data`, `stockout_gap`, `uncertain`

### 2. SKU-Level Demand Forecasting
- **LightGBM** models trained per-SKU with 14 engineered features
- **Festive calendar** and **promotional uplift** integration
- **95% confidence intervals** derived from residual error distribution (1.96 × σ)
- Rolling average fallback for sparse data
- Overall MAPE: **~10.4%**

### 3. Diwali 2023 Retrospective Analysis
- **No lookahead bias**: Detection cutoff at 2 weeks post-Diwali (Nov 7, 2023)
- **5-signal scoring system** (max 9 points):
  - Sales Dropout: +3 (≥80% drop from baseline)
  - Demand Surge: +2 (pre-Diwali spike)
  - Diwali 2022 Pattern: +2 (historical festive sensitivity)
  - Inventory Low: +1 (below lead-time threshold)
  - Promo Overlap: +1 (active promotion during Diwali)

### 4. Intelligent Reorder Engine
- **Hard constraints**: MOQ compliance, shelf-life limits, lead-time coverage
- **Strict validation**: `assert final_reorder_qty <= shelf_life_max` — **0 violations guaranteed**
- **Business impact metrics**: Revenue at risk, overstock capital trapped
- Plain-English reasoning for every recommendation

### 5. SKU Classification & ABC Analysis
- Movement classification: Fast Mover / Slow Mover / Seasonal / Dead Stock
- ABC revenue analysis with contribution percentages

### 6. Enterprise Dashboard (Tabler UI)
- 6 interactive pages with ApexCharts visualizations
- Real-time pipeline execution from UI
- Animated gradient design with glassmorphism effects
- CSV export for reorder recommendations

---

## 🚀 Quick Start

### Prerequisites
- Python 3.10+
- pip

### Installation

```bash
# Clone the repository
git clone https://github.com/Atul1242/dimand_Mirage.git
cd dimand_Mirage/project

# Install dependencies
pip install -r requirements.txt

# Launch (runs pipeline + starts web server)
python app.py
```

Open **http://localhost:5000** in your browser.

### Data Files Required
Place these CSV files in the project root (parent of `project/`):
| File | Description |
|------|-------------|
| `sales_history.csv` | Weekly sales by outlet × SKU (93,600 rows) |
| `inventory_snapshot.csv` | Current warehouse stock levels |
| `sku_master.csv` | Product metadata (shelf life, MOQ, lead time) |
| `outlet_master.csv` | Outlet details (channel, city, tier) |
| `festive_calendar.csv` | Festival dates and demand impact scores |
| `promotions_calendar.csv` | Promotional periods and uplift percentages |

---

## 📊 Dashboard Pages

| Page | What It Shows |
|------|---------------|
| **Overview** | KPI cards (stockout risk, revenue at risk, order value), top-10 stockout chart, classification donut, ABC distribution |
| **Diwali Retrospective** | Top-14 predicted stockout SKUs with signal breakdown, per-SKU sales timeline with Diwali annotations |
| **6-Week Forecast** | SKU-level forecast with historical trend, confidence bands, model selection |
| **Reorder Plan** | Full recommendations table with flags, filterable by risk type, reasoning modal, CSV export |
| **SKU Classification** | Movement categories, velocity vs consistency scatter, ABC revenue table |
| **Forecast Accuracy** | Overall MAPE, per-SKU accuracy table, MAPE distribution histogram |

---

## 🔧 Technology Stack

| Component | Technology |
|-----------|-----------|
| **Backend** | Python 3.12, Flask |
| **ML Engine** | LightGBM, scikit-learn, NumPy, Pandas |
| **Frontend** | Tabler UI (CDN), ApexCharts |
| **Fonts** | Inter (Google Fonts) |
| **Scheduling** | APScheduler-ready pipeline |

---

## 📁 Project Structure

```
project/
├── app.py                          # Flask server + API endpoints
├── pipeline.py                     # 6-step pipeline orchestrator
├── requirements.txt                # Python dependencies
├── backend/
│   ├── 1_clean_data.py             # True Zero classifier (full grid reconstruction)
│   ├── 2_forecast.py               # LightGBM demand forecaster
│   ├── 3_retrospective.py          # Diwali stockout detector (no lookahead)
│   ├── 4_reorder_engine.py         # Constraint-based reorder calculator
│   ├── 5_sku_classifier.py         # Movement + ABC classifier
│   └── 6_report_generator.py       # Monday morning report (JSON)
├── templates/
│   ├── base.html                   # Tabler UI base with gradient strips
│   ├── overview.html               # KPI dashboard
│   ├── retrospective.html          # Diwali analysis
│   ├── forecast.html               # 6-week forecast explorer
│   ├── reorder.html                # Reorder recommendations
│   ├── classification.html         # SKU classification
│   └── accuracy.html               # Forecast accuracy report
├── data/
│   ├── *.csv                       # Input data files
│   └── processed/                  # Pipeline outputs
├── docs/
│   └── true_zero_methodology.md    # Classification methodology
└── logs/
    └── pipeline_*.log              # Execution logs
```

---

## 📈 Key Metrics (Latest Run)

| Metric | Value |
|--------|-------|
| Total data points (full grid) | 1,996,800 |
| Observed sales rows | 93,600 |
| Reconstructed missing rows | 1,903,200 |
| SKUs analyzed | 40 |
| Outlets covered | 320 |
| Overall MAPE | 10.4% |
| Stockout SKUs detected | 35 |
| Shelf-life violations | **0** |
| Total order value | ₹13.9M |
| Revenue at risk | ₹19.1M |

---

## 🧪 Methodology

### True Zero Classification Logic
```
Step 1: Outlet didn't report ANY sales that week? → missing_data
Step 2: Warehouse stock ≤ 20 units + zero sales? → stockout_gap
Step 3: Outlet sells this SKU >60% of weeks + zero? → true_zero
         Outlet sells this SKU <20% of weeks + zero? → missing_data
         In between? → uncertain (treated conservatively as true_zero)
```

### Forecast Confidence Intervals
```
residuals = actual_train - predicted_train
residual_std = std(residuals)
lower_bound = max(0, forecast - 1.96 × residual_std)  # 95% CI
upper_bound = forecast + 1.96 × residual_std
```

### Reorder Validation
```python
# This assertion MUST hold for every SKU
assert final_reorder_qty <= shelf_life_max
# Enforced by clamping: qty = min(qty, shelf_life_max)
# Result: 0 shelf-life violations guaranteed
```

---

## 👥 Team

Built for the **PS 5 — The Demand Mirage** challenge.

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.
