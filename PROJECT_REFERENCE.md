# Ledgr — Project Reference

A consolidated reference covering: what the system does, how MAPE drives the entire forecasting + reordering loop, where MAPE surfaces across the UI, the enterprise-readiness audit, and the setup gotchas we hit on first run.

Use this as the single source of truth for sales conversations, onboarding new engineers, and prioritizing pre-production fixes.

---

## 1. What Ledgr Is

A demand-forecasting and inventory-orchestration system for FMCG distributors. End-to-end:

- **Android scanner** (Kotlin + ML Kit) — salesman scans retail barcodes in outlets; phone syncs over LAN to the server.
- **Flask web dashboard** (Tabler UI) — owner/manager views forecasts, reorder recommendations, stockout risk, batch expiry alerts, PO approvals (with GST routing CGST+SGST vs IGST).
- **PostgreSQL 15** — SKU/outlet master, sales history, inventory batches, purchase orders, forecast accuracy logs.
- **APScheduler worker** — Monday-morning report generator, weekly accuracy logger; can deliver to WhatsApp / Email / Telegram.
- **6-step backend pipeline** (the scripts that run on first boot):
  1. `1_clean_data` — classifies missing rows as true-zero / non-reporting / stockout-gap.
  2. `2_forecast` — LightGBM, 6-week horizon, per-SKU.
  3. `3_retrospective` — Diwali 2023 stockout backtest (10 of 14 known stockouts caught, no lookahead).
  4. `4_reorder_engine` — batch-aware available stock × MAPE-driven safety stock → reorder qty.
  5. `5_sku_classifier` — fast / slow / dead movement + ABC tiers.
  6. `6_report_generator` — executive summary JSON for the Monday report.

### Demo data shipped in the repo
- 40 SKUs with full master data (HSN, GST, supplier, lead time, MOQ, shelf life)
- 320 outlets across Pune & Nashik with channel + city + area
- 3 years of weekly sales history (~93K observed rows, expanded to ~2M-row full grid in step 1)
- 103 inventory batches (29 critical / under 14 days to expiry)
- 6-week LightGBM forecast + accuracy logs

### Tech stack at a glance
Python 3.12, Flask 3.0, SQLAlchemy 2.0, PostgreSQL 15, LightGBM, scikit-learn (RAG chatbot), Gunicorn (2 workers), Docker Compose. Android side: Kotlin + Jetpack Compose, CameraX + ML Kit, Room offline queue.

---

## 2. MAPE — Definition, Math, and Why It Matters

### What MAPE is
**Mean Absolute Percentage Error** = how far off the forecast was, expressed as a percentage of the actual value, averaged across many observations.

The formula in the code (`database.py:582`):
```python
mape = abs(actual - forecasted) / max(actual, 1) * 100
```
The `max(actual, 1)` only prevents divide-by-zero — it does not protect against MAPE exploding on near-zero weeks. A week with `actual=2, forecast=10` gives 400% MAPE. Worth knowing: weeks with very low sales can dominate the average.

### Why MAPE specifically (not MAE / RMSE)

| Metric | What it tells you | Why retail picked MAPE |
|---|---|---|
| MAE | "off by 12 units" | not comparable across SKUs of different scale |
| RMSE | "off by 12 units, big errors penalized" | same scale problem, less interpretable |
| **MAPE** | **"off by 18%"** | **scale-free — averages a 5-units/wk SKU and a 5000-units/wk SKU and the number still means the same thing** |

That's the killer feature: one number summarizes accuracy across all 40 SKUs even though they sell at wildly different volumes.

---

## 3. How MAPE Is Calculated in Ledgr

### Per-SKU MAPE — weekly contribution + 4-week rolling average

**Step A — one number per (SKU, week):**

Every Monday, the scheduler joins last week's forecast against last week's actual sales and records one row per SKU into `forecast_accuracy_log`:

```
weekly_contribution = |actual − forecast| / actual × 100
```

Example for SKU-007:
| Week | Forecast | Actual | Weekly contribution |
|---|---|---|---|
| W1 | 100 | 90  | 11.1% |
| W2 | 100 | 110 | 9.1% |
| W3 | 100 | 80  | 25.0% |
| W4 | 100 | 95  | 5.3% |

**Step B — rolling 4-week average per SKU:**

The dashboard takes the **last 4 weekly entries** for each SKU and averages them:

```
SKU-007 MAPE = (11.1 + 9.1 + 25.0 + 5.3) / 4 = 12.6%
```

That 12.6% is what shows on the accuracy page and on the SKU's individual page.

The "rolling" part matters: when a new week is logged, the oldest of the four drops off. MAPE always reflects *recent* performance, not lifetime. A SKU that was hard to forecast but stabilizes will see its number drop within a month.

### Overall MAPE — average of per-SKU MAPEs

After every SKU has its own rolling 4-week number, the system averages across all SKUs:

```
Overall MAPE = mean(SKU-001 MAPE, SKU-002 MAPE, ..., SKU-040 MAPE)
```

That's the headline number on the Overview and Forecast pages.

> **Methodology note for buyer pitches:** this is an **unweighted** average. A 50-units/week SKU with 40% MAPE drags the headline down as much as a 5000-units/week SKU. A more business-honest version would be **sales-weighted MAPE (wMAPE)** so the model's accuracy on big movers counts more. Not a bug — a known design choice that's worth upgrading before a sophisticated buyer's data scientist asks.

### Cold-start fallback (day-1 dashboards)

Before any actuals are logged, `get_forecast_accuracy_from_db()` reads `data/processed/forecast_accuracy.json` — the **test-set MAPE** computed when LightGBM first trained on held-out historical weeks. The dashboard tags this `"source": "test_set_fallback"` so users know it's not live yet.

Once 4 weeks of real actuals exist, the source switches automatically to `"database"` and the dashboard shows the live rolling number.

---

## 4. How MAPE Drives Reorder Decisions

This is the load-bearing use of MAPE. It does not just inform a dashboard — it **directly changes how much you buy each Monday**.

The reorder engine (`backend/4_reorder_engine.py:209-220`) translates each SKU's MAPE into safety-stock weeks:

| Per-SKU MAPE | Safety buffer | Interpretation |
|---|---|---|
| **< 10%** | 0.5 weeks | Forecast is sharp — barely need a buffer |
| **10–20%** | 1.0 week  | Standard — one week of cushion |
| **> 20%** | 2.0 weeks | Forecast is shaky — double up to avoid stockouts |
| *no MAPE yet* | 1.5 weeks | Default cautious until 4 weeks are logged |

Multiplied by **1.5×** if a festival is approaching (Diwali etc.) — festive demand is intrinsically harder to predict.

### Worked example — same SKU, two MAPE worlds

SKU-007 sells 100 units/week on average. Lead time 1 week, current stock 80 units, 6-week forecast = 600 units.

**Scenario A — sharp forecast (MAPE = 8%):**
```
safety_stock = 100 × 0.5 weeks = 50 units
reorder_qty  = 600 + 50 + 100 − 80 = 670 units
```

**Scenario B — shaky forecast (MAPE = 25%):**
```
safety_stock = 100 × 2.0 weeks = 200 units
reorder_qty  = 600 + 200 + 100 − 80 = 820 units
```

Same SKU, same demand — but Scenario B orders **150 units more (~22% extra)** purely because the model is less confident.

### Retrain triggers

Three thresholds across the codebase decide when to retrain the model:

| Threshold | Trigger | Where |
|---|---|---|
| Overall MAPE > 15% | System-wide retrain alert (`needs_retrain = True`) | `database.py:651` |
| Per-SKU MAPE > 25% | SKU added to `skus_flagged_for_priority_retrain` | `database.py:640` |
| Test-set MAPE > 20% | Day-1 retrain flag (cold-start) | `database.py:610` |

When MAPE drifts up over time, the model is being surprised by reality more often → time to refit LightGBM with the latest data. The scheduler checks this weekly.

### The mental model

> **MAPE is the price of trust.**
> Low MAPE → buy less safety stock → free up cash. High MAPE → buy more buffer → pay for the model's uncertainty in inventory. The reorder engine literally translates MAPE into rupees of working capital each Monday.
>
> The flip side: as the model improves, that working capital is **freed up automatically** the next Monday. The buyer isn't paying for forecasting — they're paying for an inventory policy that self-tightens as the model gets smarter.

---

## 5. Where MAPE Shows Up Across the UI

### 5.1 Sidebar — "Forecast" group
Collapsible nav section with two children:
- **Forecasts** (`/forecast`) — the working page
- **Forecast Accuracy** (`/accuracy`) — the deep-dive page

Both pages exist *because of* MAPE.

### 5.2 Overview page (`/`)
Headline KPI tile — **Overall MAPE %** — alongside Revenue at Risk, Capital Trapped, etc. The single quality score for the whole forecasting system.

If overall MAPE breaches 25%, an **alerts banner** also appears on the Overview saying *"Forecast accuracy degraded — review"* with a link to `/accuracy`. Dashboard-wide notification, not just on the forecast page.

### 5.3 Forecast page (`/forecast`)
Four KPI tiles across the top:
| Tile | What it shows |
|---|---|
| **Overall MAPE** | the headline number, color-coded (green <15%, amber 15–25%, red >25%) |
| **SKUs forecasted** | how many SKUs have a LightGBM model |
| **Low confidence** | count of SKUs with MAPE > 30% |
| **Retrain flag** | "Yes / No" — auto-trips when MAPE breaches threshold |

Plus:
- **MAPE by Category** bar chart — average MAPE grouped by SKU category (e.g. Beverages 8% vs Snacks 22%) so you can see which product family the model struggles with.
- **Most confident / Least confident SKUs** — top 5 and bottom 5 by MAPE, each clickable to drill into that SKU's performance page.

A subtitle next to the headline names the source: *"Rolling 4-week MAPE (from logs)"* once real sales come in, or *"Test-set MAPE (no actuals yet)"* on day one.

### 5.4 Forecast Accuracy page (`/accuracy`)
The dedicated deep-dive:
- Hero panel — giant system-wide MAPE % at the top.
- **MAPE Distribution by Category** chart.
- Per-SKU table showing each SKU's individual MAPE, model used (`lgbm_tuned` vs fallback), weeks of history.

This is the page to send to an operations head when they ask "is the model still working?"

### 5.5 Reorder page (`/reorder`)
MAPE doesn't always show as a labeled metric here, but it **silently changes every reorder qty on this page**. When ops asks "why are we ordering 600 units of SKU-12 when the forecast is only 300?" — the answer is "because MAPE is 28%, so we're buffering 2 weeks of demand." The accuracy page is where they go to verify.

### 5.6 SKU performance page (`/sku-performance?sku=...`)
Each SKU's detail page shows its own **Forecast MAPE** alongside weekly sales, lead time, etc. — so a category manager can judge that specific SKU's forecast trustworthiness before approving a PO.

### 5.7 Chatbot (sparkles button, bottom-right)
Ask anything like *"forecast accuracy"*, *"what's the MAPE for SKU-007"*, or *"is the model accurate?"* and it builds a Markdown report from `per_sku_mape` — overall MAPE, lightgbm vs fallback counts, low-confidence list, top/bottom SKUs.

### 5.8 Monday morning report
The auto-generated report (WhatsApp / Email / Telegram) includes the overall MAPE line in its executive summary — owner gets it on their phone without opening the app.

### Quick map — five buyer-relevant surfaces
1. **Overview** — the one-number "is the model healthy" KPI.
2. **Forecast page** — KPIs + category breakdown.
3. **Accuracy page** — the audit-trail / deep-dive.
4. **Reorder page** — silently driving safety-stock decisions.
5. **SKU performance page** — per-SKU trust signal before approving an order.

Plus the chatbot and Monday report consume it for delivery to non-dashboard users.

---

## 6. Enterprise-Readiness Audit

Findings tagged `[verified]` were checked directly against the source. Findings tagged `[reported]` came from the codebase audit and should be sanity-checked before quoting verbatim.

### Blockers — must fix before any external sales demo

| # | Issue | Location | Status |
|---|---|---|---|
| 1 | **DB connection corruption under gunicorn workers** — `psycopg2.OperationalError: insufficient data in "D" message` and `lost synchronization with server` on `/api/orders/list`, `/api/sku-list`. Single SQLAlchemy engine forked across 2 workers; no `pool_pre_ping`, no scoped session config. | `app.py`, `database.py:25-31` | `[verified in logs]` |
| 2 | **PO creation hardcodes `store_id='store-pune-001'`** — a Nashik manager creates POs scoped to Pune; multi-store integrity broken. | `app.py:1079` | `[verified]` |
| 3 | **Healthcheck `start_period=30s` vs. real first-boot 3–5 min** — Compose declares the web container unhealthy and aborts even when the stack is fine. | `Dockerfile` (verified via `docker inspect`) | `[verified]` |
| 4 | **Demo accounts hardcoded with weak passwords** (`sunrise2024`, `manager2024`, `sales2024`) — visible on login page unless `HIDE_DEMO_CREDENTIALS=1` is set; passwords guessable. | `auth.py:23-57` | `[reported]` |

### High

| # | Issue | Location | Status |
|---|---|---|---|
| 5 | **No rate limiting on API endpoints** — login can be brute-forced; `/api/run-pipeline` can be spammed. | all `/api/*` routes | `[reported]` |
| 6 | **No account lockout / failed-login counter / forced password rotation.** | `models.py:User` | `[reported]` |
| 7 | **`POSTGRES_PASSWORD=ledgr_local_dev` default in compose** — fine locally, no production guard. App enforces non-default `FLASK_SECRET_KEY` (good); DB password is not enforced. | `docker-compose.yml:18` | `[verified]` |
| 8 | **No Alembic / schema migration framework** — schema changes done by ad-hoc `ALTER TABLE` calls; multi-environment deploys break. | `database.py:57,66` | `[reported]` |
| 9 | **No audit log** — PO approve, SKU delete, inventory adjust leave no trail. Retail/GST compliance will demand "who, what, when, why." | `app.py` | `[reported]` |

### Medium

- No CI / no test suite (`tests/` missing). Regressions ship to customers. `[reported]`
- No HTTPS enforcement; Android `usesCleartextTraffic=true`. SETUP.md §8 mentions flipping it for production but doesn't ship a hardened APK. `[reported]`
- Single-tenant seeding — one store created at boot. Multi-tenant onboarding (separate chains, isolated SKUs) needs a tenant-provisioning API. `[reported]`
- No input validation on PO/SKU JSON endpoints (qty can be 0/negative, vendor unbounded). `[reported]`

### False positive caught during audit
The audit flagged `.env` containing the OpenRouter key as a "leaked secret in repo." **Not true.** Verified: `.env` is in `.gitignore` (line 16), never committed (`git ls-files .env` empty, `git log --all -- .env` empty). Local `.env` having a real key is normal and expected.

### Suggested fix order
1. Blockers 1–4 — DB pool, hardcoded store, healthcheck, demo creds.
2. High 5–9 — rate limit, lockout, prod-password guard, Alembic, audit log.
3. Medium — CI, HTTPS, multi-tenant, input validation.

---

## 7. Setup Gotchas We Hit on First Run

### 7.1 `run.ps1` parser error on Windows PowerShell 5.1
**Symptom:** `Unexpected token '}' in expression or statement` at line 63.

**Cause:** `run.ps1` contains Unicode glyphs (`▶ ✓ ⚠ ✗ ↳ —`) and was saved as UTF-8 **without BOM**. Windows PowerShell 5.1 reads `.ps1` files as legacy ANSI by default and mangles non-ASCII bytes, breaking the parser.

**Fix applied:** re-saved `run.ps1` with a UTF-8 BOM. Either of these works as a permanent fix:
```powershell
$c = [System.IO.File]::ReadAllText('run.ps1', [System.Text.Encoding]::UTF8)
[System.IO.File]::WriteAllText((Resolve-Path 'run.ps1'), $c, (New-Object System.Text.UTF8Encoding $true))
```
Or run with PowerShell 7 (`pwsh`), which defaults to UTF-8.

### 7.2 First-boot healthcheck false-fail
**Symptom:** `dependency failed to start: container ledgr-... web-1 is unhealthy` and the launcher exits 1 — even though the pipeline is still running normally and finishes a couple minutes later.

**Cause:** Dockerfile healthcheck has `start_period=30s` and `retries=3`. Real first-boot pipeline (Postgres init + schema + seed + 6-step pipeline including LightGBM forecast across 40 SKUs) takes **3–5 minutes**. Healthcheck fails 3 times → Compose marks container unhealthy → launcher aborts.

**Workaround applied:** wait it out, then manually `docker compose up -d scheduler` once `web` reports healthy.

**Permanent fix recommendation:** in the Dockerfile, change `--start-period=30s` to `--start-period=400s` (≈7 minutes). This eliminates the false failure on clean machines without affecting subsequent boots (which take ~5 seconds because pipeline outputs persist in the `pipeline_data` Docker volume).

### 7.3 Subsequent boots
Once the image is built and `pipeline_data` volume exists, `docker compose up` boots in roughly 5 seconds and the pipeline is skipped (outputs already on disk). The first-boot pain is one-time per clean machine. `docker compose down -v` wipes the volume and resets to first-boot state.

### 7.4 Scheduler "unhealthy" status is normal
Per SETUP.md §9: the scheduler container shares the web image and inherits its healthcheck (curl to `/login`), but the scheduler doesn't expose an HTTP server — so its healthcheck always fails. The scheduler is actually running fine; ignore the unhealthy status.

---

## 8. Sales-Pitch One-Liners

For when you need to explain the value in one sentence:

- **What it does:** *"Ledgr forecasts demand week-by-week per SKU per outlet, then translates forecast confidence into reorder quantities — so you carry less inventory when the model is sharp and more when it's not."*
- **Why MAPE is everywhere:** *"MAPE is the price tag on forecast trust. Every percentage point buys real rupees of working capital — Ledgr quantifies it instead of guessing it."*
- **The compounding value:** *"The buyer isn't paying for forecasting alone — they're paying for an inventory policy that self-tightens as the model gets smarter."*
- **The proof point:** *"Backtested on Diwali 2023, the system caught 10 of 14 known stockouts — without lookahead bias."*

---

## 9. Open Questions Worth Settling Before Selling

- Should overall MAPE be sales-weighted (wMAPE) instead of unweighted? Recommend yes for any buyer with a sophisticated analytics team.
- What's the multi-tenant model — one Postgres database per chain, or shared schema with row-level tenant scoping? (Currently neither — single store hardcoded.)
- SLA promise for forecast retraining cadence: weekly is current; some buyers will want daily.
- Compliance posture: GST audit trail exists in code, but no formal SOC2 / ISO27001 work yet.
