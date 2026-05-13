"""
rag.py — Lightweight retrieval layer for the Sunrise chatbot (Brief Phase 11)

Goal: replace the "dump everything as flat string into the system prompt"
pattern in app.py with chunked retrieval so token cost scales with the
*query*, not with the catalogue. We use TF-IDF + cosine over scikit-learn
(already a project dependency) instead of pulling in chromadb +
sentence-transformers (~500MB of weights), but the retrieval interface is
the same shape: build_chunks() once per pipeline run, retrieve(query, k)
on every chat turn.

Cache lives in ./data/processed/rag_cache.pkl and is rebuilt automatically
when the underlying CSV/JSON files are newer than the cache.
"""
import os
import json
import pickle
from datetime import datetime
import pandas as pd

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
PROCESSED = os.path.join(ROOT, "data", "processed")
CACHE_PATH = os.path.join(PROCESSED, "rag_cache.pkl")


def _load_pipeline_outputs():
    """Read the pipeline outputs needed to build chunks."""
    out = {}
    p = os.path.join(PROCESSED, "monday_report.json")
    if os.path.exists(p):
        with open(p) as f:
            out["report"] = json.load(f)
    p = os.path.join(PROCESSED, "forecast_accuracy.json")
    if os.path.exists(p):
        with open(p) as f:
            out["accuracy"] = json.load(f)
    p = os.path.join(PROCESSED, "top_14_stockout_skus.json")
    if os.path.exists(p):
        with open(p) as f:
            out["retro"] = json.load(f)
    p = os.path.join(PROCESSED, "reorder_recommendations.csv")
    if os.path.exists(p):
        out["reorder"] = pd.read_csv(p)
    p = os.path.join(PROCESSED, "sku_classification.csv")
    if os.path.exists(p):
        out["sku_class"] = pd.read_csv(p)
    # Outlet performance — sales_classified for weekly aggregates, outlet_master for metadata
    p = os.path.join(PROCESSED, "sales_classified.csv")
    if os.path.exists(p):
        try:
            sales = pd.read_csv(p, usecols=["outlet_id", "sku_id", "units_sold", "week_start_date"])
            sales["week_start_date"] = pd.to_datetime(sales["week_start_date"], errors="coerce")
            out["sales"] = sales
        except Exception:
            pass
    p = os.path.join(DATA, "outlet_master.csv")
    if os.path.exists(p):
        try:
            out["outlets"] = pd.read_csv(p)
        except Exception:
            pass
    p = os.path.join(DATA, "sku_master.csv")
    if os.path.exists(p):
        try:
            out["sku_master"] = pd.read_csv(p)
        except Exception:
            pass
    return out


def _build_outlet_chunks(data):
    """Outlet performance chunks: top/bottom outlets, channel rollups, latest-week
    leaderboard. Powers chatbot answers like 'best performing outlet last week'."""
    sales = data.get("sales")
    outlets = data.get("outlets")
    sku_master = data.get("sku_master")
    chunks = []
    if sales is None or outlets is None or len(sales) == 0:
        return chunks

    # Per-outlet revenue using unit_price from sku_master.
    if sku_master is not None:
        price_map = dict(zip(sku_master["sku_id"], sku_master["unit_price"]))
        sales = sales.copy()
        sales["revenue"] = sales.apply(
            lambda r: float(price_map.get(r["sku_id"], 0)) * (r["units_sold"] or 0), axis=1
        )
    else:
        sales["revenue"] = sales["units_sold"]

    last_week = sales["week_start_date"].max()
    last_8 = last_week - pd.Timedelta(weeks=8)
    iso_last_week = last_week.date().isoformat() if pd.notna(last_week) else "—"

    # Outlet metadata join helper
    omap = outlets.set_index("outlet_id")[["outlet_name", "city", "area", "channel"]].to_dict(orient="index")
    def label(oid):
        o = omap.get(oid, {})
        name = o.get("outlet_name") or oid
        return f"{oid} ({name}, {o.get('area','')} {o.get('city','')}, channel={o.get('channel','')})"

    # ── Last-week top 10 outlets by revenue ──
    last_wk = sales[sales["week_start_date"] == last_week]
    if len(last_wk) > 0:
        top10 = last_wk.groupby("outlet_id")["revenue"].sum().sort_values(ascending=False).head(10)
        lines = [f"## Top 10 best-performing outlets last week (week starting {iso_last_week}, ranked by revenue):"]
        for i, (oid, rev) in enumerate(top10.items(), 1):
            lines.append(f"{i}. {label(oid)} — Rs {int(rev):,}")
        chunks.append(("outlet_leaderboard_last_week", "\n".join(lines)))

        bot10 = last_wk.groupby("outlet_id")["revenue"].sum().sort_values().head(10)
        lines = [f"## Bottom 10 lowest-performing outlets last week (week starting {iso_last_week}):"]
        for i, (oid, rev) in enumerate(bot10.items(), 1):
            lines.append(f"{i}. {label(oid)} — Rs {int(rev):,}")
        chunks.append(("outlet_lowest_last_week", "\n".join(lines)))

    # ── 8-week top outlets ──
    last_8w = sales[sales["week_start_date"] >= last_8]
    if len(last_8w) > 0:
        top10_8w = last_8w.groupby("outlet_id")["revenue"].sum().sort_values(ascending=False).head(10)
        lines = [f"## Top 10 outlets over the last 8 weeks (ending {iso_last_week}):"]
        for i, (oid, rev) in enumerate(top10_8w.items(), 1):
            lines.append(f"{i}. {label(oid)} — Rs {int(rev):,} cumulative over 8 weeks")
        chunks.append(("outlet_leaderboard_8w", "\n".join(lines)))

    # ── Channel rollup ──
    if "channel" in outlets.columns:
        channel_map = dict(zip(outlets["outlet_id"], outlets["channel"]))
        sales_with_ch = sales.copy()
        sales_with_ch["channel"] = sales_with_ch["outlet_id"].map(channel_map)
        ch_8w = sales_with_ch[sales_with_ch["week_start_date"] >= last_8]
        ch_rev = ch_8w.groupby("channel")["revenue"].sum().sort_values(ascending=False)
        ch_count = outlets.groupby("channel").size()
        lines = [f"## Outlet channel performance — last 8 weeks (ending {iso_last_week}):"]
        for ch, rev in ch_rev.items():
            n = ch_count.get(ch, 0)
            avg_per = int(rev / max(n, 1))
            lines.append(f"- {ch}: {n} outlets, Rs {int(rev):,} cumulative, Rs {avg_per:,}/outlet average")
        chunks.append(("outlet_channel_rollup", "\n".join(lines)))

    # ── Network summary ──
    cities = outlets["city"].value_counts().to_dict() if "city" in outlets.columns else {}
    chunks.append((
        "outlet_network_summary",
        f"## Outlet network summary\nTotal {len(outlets)} outlets across cities {dict(cities)}. "
        f"Latest data week: {iso_last_week}. "
        f"Channels in use: {list(outlets['channel'].unique()) if 'channel' in outlets else []}."
    ))

    # ── Outlets that didn't report last 2 weeks ──
    weeks_sorted = sorted(sales["week_start_date"].unique())
    if len(weeks_sorted) >= 6:
        last2 = set(weeks_sorted[-2:])
        last6 = weeks_sorted[-6:]
        submitted = sales[sales["units_sold"] > 0].groupby("outlet_id")["week_start_date"].apply(set).to_dict()
        violators = []
        for oid in outlets["outlet_id"]:
            sub = submitted.get(oid, set())
            in_last6 = sum(1 for w in last6 if w in sub)
            missed_last2 = all(w not in sub for w in last2)
            if in_last6 >= 3 and missed_last2:
                violators.append(oid)
        if violators:
            sample = ", ".join(violators[:8])
            extra = "" if len(violators) <= 8 else f" (+{len(violators)-8} more)"
            chunks.append((
                "outlet_non_submitting",
                f"## Outlets not reporting recently\n{len(violators)} previously-active outlets missed the last 2 weekly uploads: {sample}{extra}."
            ))
    return chunks


def _build_supplier_chunks():
    """Supplier lead-time + festive performance from DB (matches /api/supplier-performance)."""
    chunks = []
    try:
        from sqlalchemy import create_engine, text
        import numpy as np
        db_uri = os.environ.get("DATABASE_URL", f"sqlite:///{os.path.join(ROOT, 'sunrise.db')}")
        if db_uri.startswith("postgres://"):
            db_uri = "postgresql://" + db_uri[len("postgres://"):]
        eng = create_engine(db_uri, future=True)
        with eng.connect() as conn:
            rows = conn.execute(text("""
                SELECT s.brand, s.product_name, s.sku_code, s.moq_from_supplier,
                       l.order_placed_date, l.actual_receipt_date
                  FROM supplier_lead_time_log l
                  JOIN skus s ON l.sku_id = s.id
                 WHERE l.actual_receipt_date IS NOT NULL
            """)).fetchall()
        if not rows:
            return chunks
        by_brand = {}
        for brand, name, code, moq, op, ar in rows:
            if not op or not ar:
                continue
            try:
                lt = (pd.to_datetime(ar) - pd.to_datetime(op)).days
            except Exception:
                continue
            by_brand.setdefault(brand, []).append(lt)
        if not by_brand:
            return chunks
        all_lt = [x for arr in by_brand.values() for x in arr]
        avg = float(np.mean(all_lt))
        p80 = float(np.percentile(all_lt, 80))
        festive_p80 = round(p80 * 1.3, 1)
        lines = [
            f"SUPPLIER_PERFORMANCE supplier vendor lead time delivery speed reliability scorecard: "
            f"{len(by_brand)} suppliers tracked. "
            f"Avg lead time {avg:.1f}d, P80 (worst-case) {p80:.1f}d, festive Oct-Nov estimate {festive_p80}d."
        ]
        for brand, arr in sorted(by_brand.items(), key=lambda kv: -np.mean(kv[1])):
            avg_b = float(np.mean(arr))
            min_b = min(arr); max_b = max(arr); var = max_b - min_b
            tag = "Volatile" if var > 5 else "Moderate" if var > 2 else "Tight"
            lines.append(f"  {brand}: avg {avg_b:.1f}d (min {min_b}d / max {max_b}d), variance {var}d ({tag}).")
        chunks.append(("supplier_performance", " ".join(lines)))
    except Exception:
        pass
    return chunks


def _build_batch_chunks():
    """Near-expiry batches from DB."""
    chunks = []
    try:
        from sqlalchemy import create_engine, text
        from datetime import date
        db_uri = os.environ.get("DATABASE_URL", f"sqlite:///{os.path.join(ROOT, 'sunrise.db')}")
        if db_uri.startswith("postgres://"):
            db_uri = "postgresql://" + db_uri[len("postgres://"):]
        eng = create_engine(db_uri, future=True)
        with eng.connect() as conn:
            rows = conn.execute(text("""
                SELECT s.sku_code, s.product_name, b.batch_no, b.qty_received,
                       b.expiry_date
                  FROM batches b
                  JOIN skus s ON b.sku_id = s.id
            """)).fetchall()
        if not rows:
            return chunks
        today = date.today()
        critical = []  # < 14 days
        warning = []   # 14-30 days
        ok_ct = 0
        for code, name, bno, qty, exp in rows:
            if not exp:
                continue
            try:
                expd = pd.to_datetime(exp).date()
            except Exception:
                continue
            d = (expd - today).days
            entry = (code, name, bno, qty, d)
            if d < 14: critical.append(entry)
            elif d < 30: warning.append(entry)
            else: ok_ct += 1
        lines = [f"BATCH_EXPIRY: {len(rows)} total batches. {len(critical)} critical (<14d), "
                 f"{len(warning)} warning (14-30d), {ok_ct} healthy."]
        if critical:
            lines.append("Critical:")
            for code, name, bno, qty, d in sorted(critical, key=lambda x: x[4])[:8]:
                lines.append(f"  {code} {name} batch {bno} — {qty} units, {d}d to expiry.")
        chunks.append(("batch_expiry", " ".join(lines)))
    except Exception:
        pass
    return chunks


def _build_po_chunks():
    """Purchase orders summary from DB."""
    chunks = []
    try:
        from sqlalchemy import create_engine, text
        db_uri = os.environ.get("DATABASE_URL", f"sqlite:///{os.path.join(ROOT, 'sunrise.db')}")
        if db_uri.startswith("postgres://"):
            db_uri = "postgresql://" + db_uri[len("postgres://"):]
        eng = create_engine(db_uri, future=True)
        with eng.connect() as conn:
            rows = conn.execute(text("""
                SELECT po_number, po_status, supplier_name, qty_ordered, total_value,
                       cgst_rate, igst_rate, created_date
                  FROM purchase_orders
                 ORDER BY created_date DESC
            """)).fetchall()
        if not rows:
            return chunks
        by_po = {}
        for pn, status, sup, qty, tot, cgst, igst, cd in rows:
            g = by_po.setdefault(pn, {"status": status, "supplier": sup, "items": 0,
                                     "qty": 0, "total": 0.0, "interstate": float(igst or 0) > 0,
                                     "date": str(cd) if cd else ""})
            g["items"] += 1
            g["qty"] += int(qty or 0)
            g["total"] += float(tot or 0)
        # Status counts
        from collections import Counter
        sts = Counter(g["status"] for g in by_po.values())
        lines = [
            f"PURCHASE_ORDERS PO purchase order draft approved received status pending count: "
            f"{len(by_po)} POs in system. "
            f"By status: {dict(sts)}. "
            f"Total value Rs {int(sum(g['total'] for g in by_po.values())):,}."
        ]
        # Recent POs
        recent = list(by_po.items())[:8]
        for pn, g in recent:
            tax = "IGST" if g["interstate"] else "CGST+SGST"
            lines.append(f"  {pn} {g['status']} {g['supplier']} {g['items']}-line {g['qty']} units Rs {int(g['total']):,} ({tax}, {g['date']}).")
        chunks.append(("purchase_orders", " ".join(lines)))
    except Exception:
        pass
    return chunks


def _build_pipeline_chunks():
    """Pipeline run history from DB."""
    chunks = []
    try:
        from sqlalchemy import create_engine, text
        db_uri = os.environ.get("DATABASE_URL", f"sqlite:///{os.path.join(ROOT, 'sunrise.db')}")
        if db_uri.startswith("postgres://"):
            db_uri = "postgresql://" + db_uri[len("postgres://"):]
        eng = create_engine(db_uri, future=True)
        with eng.connect() as conn:
            rows = conn.execute(text("""
                SELECT started_at, completed_at, status, step_reached, error_message
                  FROM pipeline_runs
                 ORDER BY started_at DESC
                 LIMIT 5
            """)).fetchall()
        if not rows:
            chunks.append(("pipeline_status", "PIPELINE: No runs logged yet. The 6-step pipeline (clean → forecast → retro → reorder → classify → report) runs Mondays 7:45 AM IST or on-demand from the dashboard."))
            return chunks
        lines = [f"PIPELINE_RUN_HISTORY (last {len(rows)}):"]
        for sa, ca, st, sr, err in rows:
            elapsed = ""
            try:
                if sa and ca:
                    elapsed = f", {(pd.to_datetime(ca)-pd.to_datetime(sa)).total_seconds():.0f}s"
            except Exception:
                pass
            err_str = f", error: {err[:80]}" if err else ""
            lines.append(f"  {sa or '?'} → {st}, step {sr}/6{elapsed}{err_str}.")
        chunks.append(("pipeline_status", " ".join(lines)))
    except Exception:
        pass
    return chunks


def _build_data_quality_chunks(data):
    """Data quality summary from sales_classified.csv."""
    chunks = []
    try:
        sales_path = os.path.join(PROCESSED, "sales_classified.csv")
        if not os.path.exists(sales_path):
            return chunks
        df = pd.read_csv(sales_path, usecols=["row_classification", "week_start_date"])
        cls = df["row_classification"].value_counts().to_dict()
        accepted = cls.get("observed", 0) + cls.get("true_zero", 0)
        rejected = cls.get("stockout_gap", 0) + cls.get("uncertain_excluded", 0)
        missing = cls.get("missing_data", 0)
        denom = accepted + rejected
        rate = (accepted / denom * 100) if denom > 0 else 0
        chunks.append((
            "data_quality",
            f"DATA_QUALITY: {accepted:,} accepted, {rejected:,} rejected, {missing:,} missing/never-collected. "
            f"Acceptance rate {rate:.1f}% over actually-collected rows. "
            f"Classification breakdown: {cls}."
        ))
    except Exception:
        pass
    return chunks


def _build_forecast_chunks(data):
    """Forecast horizon (6-week aggregate) chunk."""
    chunks = []
    try:
        fc_path = os.path.join(PROCESSED, "forecasts.csv")
        if not os.path.exists(fc_path):
            return chunks
        fc = pd.read_csv(fc_path)
        weekly = fc.groupby("week_start_date")["forecasted_units"].sum().sort_index()
        lines = ["FORECAST_HORIZON (next 6 weeks, aggregated across all SKUs):"]
        for wk, units in weekly.items():
            lines.append(f"  {wk}: {int(units)} units forecasted.")
        chunks.append(("forecast_horizon", " ".join(lines)))
    except Exception:
        pass
    return chunks


def _build_classification_report_chunk():
    """True-zero / missing-data breakdown."""
    chunks = []
    try:
        path = os.path.join(PROCESSED, "classification_report.json")
        if not os.path.exists(path):
            return chunks
        with open(path) as f:
            r = json.load(f)
        chunks.append((
            "classification_report",
            f"CLASSIFICATION_REPORT: {r.get('total_rows',0):,} total grid rows. "
            f"Originally observed: {r.get('original_observed_rows',0):,}, reconstructed: {r.get('reconstructed_rows',0):,}. "
            f"Counts by class: {r.get('classification_counts', {})}. "
            f"Coverage: {r.get('unique_skus',0)} SKUs × {r.get('unique_outlets',0)} outlets × {r.get('unique_weeks',0)} weeks. "
            f"Date range: {r.get('date_range', {})}."
        ))
    except Exception:
        pass
    return chunks


def build_chunks():
    """Build all RAG chunks the chatbot can retrieve. Covers every data
    source the dashboard pages render: SKUs (reorder/classification/accuracy),
    outlets (leaderboards/channels), suppliers, batches, purchase orders,
    pipeline run history, data quality, forecasts, retrospective."""
    data = _load_pipeline_outputs()
    chunks = []

    rep = data.get("report", {})
    es = rep.get("executive_summary", {}) if rep else {}
    if es:
        summary = (
            f"EXECUTIVE_SUMMARY for {rep.get('report_date','')}. "
            f"SKUs to reorder: {es.get('total_skus_to_reorder',0)}. "
            f"Total order value INR: {es.get('total_order_value_inr',0)}. "
            f"Stockout risk SKUs: {es.get('skus_at_stockout_risk',0)}. "
            f"Overstock risk SKUs: {es.get('skus_at_overstock_risk',0)}. "
            f"Total revenue at risk INR: {es.get('total_revenue_at_risk_inr', es.get('revenue_at_risk_inr',0))}. "
            f"Capital trapped in overstock INR: {es.get('capital_trapped_in_overstock_inr',0)}. "
            f"Dead stock count: {es.get('dead_stock_count',0)}. "
            f"Shelf life violations: {es.get('shelf_life_violations',0)}. "
            f"Total SKUs analyzed: {es.get('total_skus_analyzed',0)}."
        )
        chunks.append(("__summary__", summary))

    reorder = data.get("reorder")
    sku_class = data.get("sku_class")
    accuracy = data.get("accuracy", {}).get("per_sku_mape", {})

    if reorder is not None and len(reorder) > 0:
        for _, r in reorder.iterrows():
            sku = str(r.get("sku_id", ""))
            parts = [
                f"SKU {sku} {r.get('product_name','')} {r.get('brand','')} {r.get('category','')}.",
                f"Available stock: {int(r.get('available_stock',0))} units, weeks of stock: {r.get('weeks_of_stock','')}.",
                f"6-week forecast: {int(r.get('forecast_6w_total',0))} units. "
                f"Reorder qty: {int(r.get('final_reorder_qty',0))} units, order value Rs {int(r.get('order_value_inr',0))}.",
                f"Flags: {r.get('flags','')}. {r.get('reason_text','')}",
            ]
            if sku_class is not None and len(sku_class) > 0:
                row = sku_class[sku_class["sku_id"] == sku]
                if len(row) > 0:
                    s = row.iloc[0]
                    parts.append(
                        f"Movement class: {s.get('movement_class','')}, ABC: {s.get('abc_class','')}, "
                        f"Avg weekly sales: {s.get('avg_weekly_sales',0)}, Total revenue Rs {s.get('total_revenue',0)}."
                    )
            if sku in accuracy:
                a = accuracy[sku]
                parts.append(f"Forecast MAPE: {a.get('mape',0)}%, model: {a.get('model_used','')}.")
            chunks.append((sku, " ".join(parts)))

    retro = data.get("retro", {})
    for s in retro.get("predicted_stockout_skus", []):
        chunks.append((
            f"retro:{s.get('sku_id','')}",
            f"DIWALI_RETRO {s.get('sku_id','')} {s.get('product_name','')}: "
            f"score {s.get('stockout_score',0)}/9, signals: {s.get('signals_triggered','')}. "
            f"{s.get('reasoning','')}"
        ))
    # Aggregated retrospective recall
    racc = retro.get("accuracy", {})
    if racc:
        chunks.append((
            "retro_summary",
            f"DIWALI_RETROSPECTIVE_SUMMARY: detected {racc.get('correctly_identified',0)}/{racc.get('known_stockout_count',14)} "
            f"of the known stockout SKUs in the top 14. "
            f"Missed: {racc.get('missed_skus', [])}. False positives: {racc.get('false_positives', [])}. "
            f"Detection cutoff is 2 weeks post-Diwali (no lookahead bias)."
        ))

    # Outlet, supplier, batch, PO, pipeline, data-quality, forecast, classification
    chunks.extend(_build_outlet_chunks(data))
    chunks.extend(_build_supplier_chunks())
    chunks.extend(_build_batch_chunks())
    chunks.extend(_build_po_chunks())
    chunks.extend(_build_pipeline_chunks())
    chunks.extend(_build_data_quality_chunks(data))
    chunks.extend(_build_forecast_chunks(data))
    chunks.extend(_build_classification_report_chunk())

    return chunks


def _is_cache_fresh():
    if not os.path.exists(CACHE_PATH):
        return False
    cache_mtime = os.path.getmtime(CACHE_PATH)
    sources = [
        os.path.join(PROCESSED, f) for f in
        ("monday_report.json", "forecast_accuracy.json", "top_14_stockout_skus.json",
         "reorder_recommendations.csv", "sku_classification.csv")
    ]
    for s in sources:
        if os.path.exists(s) and os.path.getmtime(s) > cache_mtime:
            return False
    return True


def get_index(rebuild=False):
    """Return (vectorizer, matrix, chunks). Caches to disk between requests."""
    if not HAS_SKLEARN:
        return None, None, build_chunks()
    if not rebuild and _is_cache_fresh():
        try:
            with open(CACHE_PATH, "rb") as f:
                return pickle.load(f)
        except Exception:
            pass
    chunks = build_chunks()
    if not chunks:
        return None, None, []
    texts = [t for _, t in chunks]
    vec = TfidfVectorizer(ngram_range=(1, 2), min_df=1, lowercase=True)
    matrix = vec.fit_transform(texts)
    bundle = (vec, matrix, chunks)
    try:
        os.makedirs(PROCESSED, exist_ok=True)
        with open(CACHE_PATH, "wb") as f:
            pickle.dump(bundle, f)
    except Exception:
        pass
    return bundle


def retrieve(query, k=5):
    """Return top-k chunks most relevant to the query. Falls back to the
    summary chunk if sklearn is unavailable or index is empty."""
    vec, matrix, chunks = get_index()
    if not chunks:
        return []
    if vec is None or matrix is None:
        return chunks[:1]
    q = vec.transform([query])
    sims = cosine_similarity(q, matrix).ravel()
    top_idx = sims.argsort()[::-1][:k]
    out = []
    summary_already = False
    for i in top_idx:
        if sims[i] <= 0:
            continue
        cid, text = chunks[i]
        if cid == "__summary__":
            summary_already = True
        out.append((cid, text, float(sims[i])))
    # Always include the executive summary as anchor context, if present
    if not summary_already:
        for cid, text in chunks:
            if cid == "__summary__":
                out.insert(0, (cid, text, 1.0))
                break
    return out


def build_context(query, k=5, max_chars=6000):
    """Format retrieved chunks as natural-language sections for the LLM.
    Capped at max_chars to keep the system prompt bounded. Relevance scores
    are intentionally hidden — they confused the LLM into ignoring real data."""
    hits = retrieve(query, k=k)
    if not hits:
        return ""
    pieces = []
    used = 0
    for cid, text, _score in hits:
        section = text  # already prose-formatted from build_chunks
        if used + len(section) > max_chars:
            break
        pieces.append(section)
        used += len(section)
    return "\n\n".join(pieces)
