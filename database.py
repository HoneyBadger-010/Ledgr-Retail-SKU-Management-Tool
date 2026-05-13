"""
database.py — SQLAlchemy Database Layer (Brief Phase 1)

Bridges CSV data and PostgreSQL. On startup:
1. Initializes SQLAlchemy tables via create_all()
2. Seeds data from existing CSVs into PostgreSQL
3. Provides query helpers that ALL API routes use

This replaces pd.read_csv() everywhere in app.py.
"""
import os, json, logging
from datetime import datetime, timedelta
import pandas as pd
from models import (db, SKU, Outlet, SalesHistory, InventorySnapshot, Batch,
                    ReorderRecommendation, ForecastAccuracyLog, DataQualityLog,
                    SupplierLeadTimeLog, PipelineRun, PurchaseOrder, User, Store,
                    UserRole, PipelineStatus)

logger = logging.getLogger('database')
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
PROCESSED = os.path.join(ROOT, "data", "processed")


def init_db(app):
    """Initialize SQLAlchemy with the Flask app. Schema is created synchronously
    (cheap — empty tables), but the 93K-row CSV seed is deferred to a background
    thread so gunicorn binds + serves /login within the platform healthcheck
    window. The demo login still works during seeding because DEMO_USERS is
    in-memory; dashboard data pages just render empty until the seed completes.
    """
    db_uri = os.environ.get("DATABASE_URL",
        f"sqlite:///{os.path.join(ROOT, 'sunrise.db')}")
    # Railway, Heroku, and a few other hosts hand out the legacy "postgres://"
    # scheme. SQLAlchemy 2.x only accepts "postgresql://" — normalize so the
    # platform-supplied env var works without manual editing.
    if db_uri.startswith("postgres://"):
        db_uri = "postgresql://" + db_uri[len("postgres://"):]
    # Redact the credentials portion before printing.
    redacted = db_uri.split("@")[-1] if "@" in db_uri else db_uri
    print(f"[init_db] connecting to: ...@{redacted}", flush=True)
    app.config["SQLALCHEMY_DATABASE_URI"] = db_uri
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    with app.app_context():
        print("[init_db] creating tables...", flush=True)
        db.create_all()
        print("[init_db] adding missing GST/supplier columns...", flush=True)
        _ensure_gst_columns()
        print("[init_db] schema ready", flush=True)

    def _bg_seed():
        with app.app_context():
            try:
                print("[init_db-bg] seeding from CSVs...", flush=True)
                _seed_if_empty()
                print("[init_db-bg] seed complete", flush=True)
            except Exception as e:
                print(f"[init_db-bg] seed failed (non-fatal): {e}", flush=True)
                import traceback; traceback.print_exc()

    if os.environ.get("LEDGR_SYNC_SEED", "").lower() in ("1", "true", "yes"):
        _bg_seed()
    else:
        import threading
        threading.Thread(target=_bg_seed, name="ledgr-bg-seed", daemon=True).start()
    logger.info(f"Database initialized: {redacted}")


def _ensure_gst_columns():
    """Cross-DB migration: add supplier_*/state/gstin columns to existing
    tables when SQLAlchemy create_all() can't (it only creates new tables).
    Uses the dialect-neutral Inspector so the same code works on SQLite and
    Postgres (Docker)."""
    from sqlalchemy import text, inspect
    try:
        insp = inspect(db.engine)
        existing_tables = set(insp.get_table_names())
        with db.engine.begin() as conn:
            if "skus" in existing_tables:
                sku_cols = {c["name"] for c in insp.get_columns("skus")}
                for col, decl in [
                    ("supplier_name",   "VARCHAR(255)"),
                    ("supplier_gstin",  "VARCHAR(20)"),
                    ("supplier_state",  "VARCHAR(50)"),
                ]:
                    if col not in sku_cols:
                        conn.execute(text(f"ALTER TABLE skus ADD COLUMN {col} {decl}"))
                        logger.info(f"  added skus.{col}")
            if "stores" in existing_tables:
                store_cols = {c["name"] for c in insp.get_columns("stores")}
                for col, decl in [
                    ("state", "VARCHAR(50) DEFAULT 'Maharashtra'"),
                    ("gstin", "VARCHAR(20)"),
                ]:
                    if col not in store_cols:
                        conn.execute(text(f"ALTER TABLE stores ADD COLUMN {col} {decl}"))
                        logger.info(f"  added stores.{col}")
    except Exception as e:
        logger.warning(f"GST column migration skipped ({e})")


def _seed_if_empty():
    """Seed PostgreSQL tables from CSVs if they are empty."""
    # Only seed if SKU table is empty (first run)
    if SKU.query.first() is not None:
        logger.info("Database already seeded - skipping")
        return
    logger.info("=" * 60)
    logger.info("SEEDING DATABASE FROM CSV FILES")
    logger.info("=" * 60)

    # 1. Default store (Pune is in Maharashtra; set state for GST compliance)
    store = Store(id='store-pune-001', name='Sunrise Pune', city='Pune',
                  state='Maharashtra', gstin='27AAACS1234A1Z5')
    db.session.add(store)
    # Flush so the Store row is visible to subsequent FK inserts on Postgres
    # (autoflush during seeding would otherwise raise ForeignKeyViolation).
    db.session.flush()
    store_id = store.id

    # 2. SKU Master
    sku_path = os.path.join(DATA, "sku_master.csv")
    if os.path.exists(sku_path):
        df = pd.read_csv(sku_path)
        for _, r in df.iterrows():
            sku = SKU(
                sku_code=str(r.get("sku_id", "")),
                product_name=str(r.get("product_name", "")),
                brand=str(r.get("brand", "")),
                category=str(r.get("category", "")),
                unit_price=float(r.get("unit_price", 0)),
                cost_price=float(r.get("cost_price", 0)),
                shelf_life_days=int(r.get("shelf_life_days", 365)),
                moq_from_supplier=int(r.get("moq_from_supplier", 6)),
                supplier_lead_time_days=int(r.get("supplier_lead_time_days", 7)),
                store_id=store_id
            )
            db.session.add(sku)
        logger.info(f"  Seeded {len(df)} SKUs")

    # 3. Outlet Master
    outlet_path = os.path.join(DATA, "outlet_master.csv")
    if os.path.exists(outlet_path):
        df = pd.read_csv(outlet_path)
        for _, r in df.iterrows():
            outlet = Outlet(
                outlet_code=str(r.get("outlet_id", "")),
                outlet_type=str(r.get("outlet_type", "")),
                city=str(r.get("city", "")),
                area=str(r.get("area", "")),
                channel=str(r.get("channel", "kirana")),
                store_id=store_id
            )
            db.session.add(outlet)
        logger.info(f"  Seeded {len(df)} outlets")

    # 4. Inventory Snapshots
    inv_path = os.path.join(DATA, "inventory_snapshot.csv")
    if os.path.exists(inv_path):
        df = pd.read_csv(inv_path)
        for _, r in df.iterrows():
            sku_obj = SKU.query.filter_by(sku_code=str(r.get("sku_id",""))).first()
            if sku_obj:
                snap = InventorySnapshot(
                    sku_id=sku_obj.id,
                    warehouse_stock=int(r.get("warehouse_stock", 0)),
                    in_transit_qty=int(r.get("in_transit_qty", 0)),
                    committed_qty=int(r.get("committed_qty", 0)),
                    snapshot_date=datetime.utcnow().date(),
                    store_id=store_id
                )
                db.session.add(snap)
        logger.info(f"  Seeded {len(df)} inventory snapshots")

    # 5. Seed batches from inventory (real batch data, not random)
    _seed_batches(store_id)

    # 6. Seed supplier lead time logs from SKU master
    _seed_supplier_logs(store_id)

    # 7. Backfill GST + supplier metadata so the reorder/PO flow works for
    # every SKU out of the box (Brief Part 6A — defaults, owner can override).
    _backfill_gst_supplier_defaults()

    db.session.commit()
    
    # Verify seeding was successful
    sku_count = SKU.query.count()
    outlet_count = Outlet.query.count()
    store_count = Store.query.count()
    logger.info("=" * 60)
    logger.info("DATABASE SEEDING COMPLETE")
    logger.info("=" * 60)
    logger.info(f"Loaded: {store_count} stores, {sku_count} SKUs, {outlet_count} outlets")
    
    if sku_count == 0:
        logger.error("WARNING: No SKUs were loaded! Check CSV files in data/ directory")
    else:
        logger.info("✓ Database seeding successful")


_CATEGORY_HSN = {
    "personal_care":  "3401",  # soaps, shampoos
    "household":      "3402",  # cleaning agents
    "packaged_food":  "1905",  # bakery / packaged biscuits
}


def _backfill_gst_supplier_defaults():
    """Populate supplier_name/state/gstin and HSN/GST for any SKU missing them.
    Idempotent — won't overwrite values an owner has already set."""
    import hashlib
    for s in SKU.query.all():
        if not s.supplier_name and s.brand:
            s.supplier_name = s.brand
        if not s.supplier_state:
            s.supplier_state = "Maharashtra"
        if not s.supplier_gstin and s.brand:
            digest = hashlib.sha1(s.brand.encode()).hexdigest().upper()[:13]
            s.supplier_gstin = "27" + digest
        if not s.hsn_code:
            s.hsn_code = _CATEGORY_HSN.get((s.category or "").lower(), "9999")
        if not s.gst_rate:
            s.gst_rate = 18.0


def _seed_batches(store_id):
    """Create real batch records from inventory + shelf life data."""
    skus = SKU.query.filter_by(store_id=store_id).all()
    for sku in skus:
        inv = InventorySnapshot.query.filter_by(sku_id=sku.id).first()
        if not inv or inv.warehouse_stock <= 0:
            continue
        shelf_days = sku.shelf_life_days or 365
        # Create 1-3 batches per SKU based on stock level
        stock = inv.warehouse_stock
        num_batches = min(3, max(1, stock // 50))
        per_batch = stock // num_batches
        for i in range(num_batches):
            # Stagger receipt dates: oldest batch first
            days_ago = shelf_days // 3 * (num_batches - i)
            receipt = datetime.utcnow() - timedelta(days=min(days_ago, shelf_days - 10))
            expiry = receipt + timedelta(days=shelf_days)
            batch = Batch(
                sku_id=sku.id,
                batch_no=f"B-{sku.sku_code[-3:]}-{receipt.strftime('%m%d')}-{i+1}",
                mfd_date=receipt.date(),
                expiry_date=expiry.date(),
                qty_received=per_batch + (stock % num_batches if i == 0 else 0),
                receipt_date=receipt.date(),
                store_id=store_id
            )
            db.session.add(batch)
    logger.info("  Seeded batch records from inventory/shelf life data")


def _seed_supplier_logs(store_id):
    """Seed supplier lead time log with historical records for P80 calculation."""
    skus = SKU.query.filter_by(store_id=store_id).all()
    import random
    for sku in skus:
        base_lt = sku.supplier_lead_time_days or 7
        # Generate 8 historical lead time records per SKU
        for i in range(8):
            days_ago = 7 * (8 - i)
            order_date = datetime.utcnow() - timedelta(days=days_ago + base_lt + 3)
            expected = order_date + timedelta(days=base_lt)
            # Simulate actual delivery variance: -2 to +5 days
            variance = random.randint(-2, 5)
            actual = expected + timedelta(days=variance)
            log = SupplierLeadTimeLog(
                sku_id=sku.id,
                order_placed_date=order_date.date(),
                expected_receipt_date=expected.date(),
                actual_receipt_date=actual.date(),
                store_id=store_id
            )
            db.session.add(log)
    logger.info("  Seeded supplier lead time logs")


# ── Query Helpers (replace pd.read_csv everywhere) ──

def _scope(query, model, store_ids):
    """Apply store-scoping filter unless store_ids is None (admin/system)."""
    if store_ids is None:
        return query
    if not store_ids:
        return query.filter(False)  # explicit empty list -> no rows
    return query.filter(model.store_id.in_(store_ids))


def get_sku_list(store_ids=None):
    """Get SKUs scoped to the user's stores."""
    q = _scope(SKU.query, SKU, store_ids)
    return [{"sku_id": s.sku_code, "product_name": s.product_name,
             "brand": s.brand, "category": s.category,
             "unit_price": float(s.unit_price or 0),
             "cost_price": float(s.cost_price or 0),
             "shelf_life_days": s.shelf_life_days,
             "moq_from_supplier": s.moq_from_supplier,
             "supplier_lead_time_days": s.supplier_lead_time_days,
             "db_id": s.id} for s in q.all()]


def get_batch_expiry(store_ids=None):
    """Get real batch expiry data from the batches table — NOT random."""
    q = db.session.query(Batch, SKU).join(SKU, Batch.sku_id == SKU.id)
    if store_ids is not None:
        if not store_ids:
            return []
        q = q.filter(Batch.store_id.in_(store_ids))
    results = []
    today = datetime.utcnow().date()
    for batch, sku in q.all():
        if not batch.expiry_date:
            continue
        days_to_expiry = (batch.expiry_date - today).days
        status = ("expired" if days_to_expiry < 0 else
                  "critical" if days_to_expiry < 14 else
                  "warning" if days_to_expiry < 30 else "ok")
        results.append({
            "sku_id": sku.sku_code,
            "product_name": sku.product_name,
            "brand": sku.brand,
            "category": sku.category,
            "batch_no": batch.batch_no,
            "qty": batch.qty_received,
            "mfd_date": batch.mfd_date.isoformat() if batch.mfd_date else "",
            "expiry_date": batch.expiry_date.isoformat() if batch.expiry_date else "",
            "days_to_expiry": days_to_expiry,
            "status": status,
            "shelf_life_days": sku.shelf_life_days
        })
    return sorted(results, key=lambda x: x["days_to_expiry"])


def get_supplier_lead_times(store_ids=None):
    """Get actual supplier lead times with P80 calculation from DB logs."""
    import numpy as np
    q = db.session.query(SupplierLeadTimeLog, SKU).join(
        SKU, SupplierLeadTimeLog.sku_id == SKU.id)
    if store_ids is not None:
        if not store_ids:
            return {"avg_lead_time": 0, "p80_lead_time": 0,
                    "festive_avg_lead_time": 0, "supplier_count": 0,
                    "suppliers": [], "sku_details": []}
        q = q.filter(SupplierLeadTimeLog.store_id.in_(store_ids))

    sku_lead_times = {}
    for log, sku in q.all():
        if log.actual_receipt_date and log.order_placed_date:
            actual_lt = (log.actual_receipt_date - log.order_placed_date).days
            if sku.sku_code not in sku_lead_times:
                sku_lead_times[sku.sku_code] = {"name": sku.product_name,
                    "brand": sku.brand, "times": [], "moq": sku.moq_from_supplier}
            sku_lead_times[sku.sku_code]["times"].append(actual_lt)

    all_times = []
    sku_details = []
    for sku_code, data in sku_lead_times.items():
        times = data["times"]
        all_times.extend(times)
        p80 = float(np.percentile(times, 80)) if times else 0
        sku_details.append({
            "sku_id": sku_code, "brand": data["brand"],
            "product_name": data["name"],
            "lead_time": round(sum(times)/len(times), 1),
            "p80_lead_time": round(p80, 1),
            "min_lt": min(times), "max_lt": max(times),
            "moq": data["moq"], "records": len(times)
        })

    avg_lt = round(sum(all_times)/len(all_times), 1) if all_times else 7
    p80_lt = round(float(np.percentile(all_times, 80)), 1) if all_times else 9
    festive_avg = round(p80_lt * 1.3, 1)  # Brief Part 5C: P80 during Diwali

    # Group by brand as supplier
    brand_map = {}
    for d in sku_details:
        b = d["brand"]
        if b not in brand_map:
            brand_map[b] = {"name": b, "times": [], "skus": 0, "moqs": []}
        brand_map[b]["times"].append(d["lead_time"])
        brand_map[b]["skus"] += 1
        brand_map[b]["moqs"].append(d["moq"])

    suppliers = []
    for b, data in brand_map.items():
        suppliers.append({
            "name": b, "sku_count": data["skus"],
            "avg_lt": round(sum(data["times"])/len(data["times"]), 1),
            "min_lt": round(min(data["times"])), "max_lt": round(max(data["times"])),
            "avg_moq": round(sum(data["moqs"])/len(data["moqs"]))
        })

    return {
        "avg_lead_time": avg_lt, "p80_lead_time": p80_lt,
        "festive_avg_lead_time": festive_avg,
        "supplier_count": len(suppliers),
        "suppliers": suppliers, "sku_details": sku_details
    }


def create_sku(data, store_id=None):
    """Create a new SKU in the DB. store_id must be one the user has access to.
    CSV export happens lazily at pipeline startup (export_db_to_csv) — no
    per-mutation CSV writes (Brief C8 fix: DB is source of truth)."""
    if store_id is None:
        store_id = 'store-pune-001'
    sku_code = (data.get("sku_code") or data.get("sku_id") or "").strip()
    if not sku_code:
        return False, "SKU code is required"
    existing = SKU.query.filter_by(sku_code=sku_code, store_id=store_id).first()
    if existing:
        return False, f"{sku_code} already exists"
    sku = SKU(
        sku_code=sku_code,
        product_name=data.get("product_name", ""),
        brand=data.get("brand", ""),
        category=data.get("category", ""),
        unit_price=float(data.get("unit_price", 0) or 0),
        cost_price=float(data.get("cost_price", 0) or 0),
        shelf_life_days=int(data.get("shelf_life_days", 365) or 365),
        moq_from_supplier=int(data.get("moq_from_supplier", 6) or 6),
        supplier_lead_time_days=int(data.get("supplier_lead_time_days", 7) or 7),
        hsn_code=(data.get("hsn_code") or None),
        gst_rate=(float(data["gst_rate"]) if data.get("gst_rate") not in (None, "") else None),
        supplier_name=(data.get("supplier_name") or None),
        supplier_gstin=(data.get("supplier_gstin") or None),
        supplier_state=(data.get("supplier_state") or None),
        store_id=store_id
    )
    db.session.add(sku)
    db.session.commit()
    return True, f"SKU {sku_code} added successfully"


def delete_sku(sku_code, store_ids=None):
    """Delete a SKU from the DB. The SKU must belong to one of the user's
    store_ids (Brief Part 2B: cross-store access blocked at the query layer)."""
    q = SKU.query.filter_by(sku_code=sku_code)
    if store_ids is not None:
        if not store_ids:
            return False, "no store access"
        q = q.filter(SKU.store_id.in_(store_ids))
    sku = q.first()
    if not sku:
        return False, f"{sku_code} not found"
    db.session.delete(sku)
    db.session.commit()
    return True, f"SKU {sku_code} deleted"


def export_db_to_csv():
    """Brief C8 fix: write the canonical DB tables out as CSVs that the
    pipeline backend scripts read. Called once at the start of run_pipeline()
    so the pipeline always sees the latest DB state."""
    try:
        skus = SKU.query.all()
        if skus:
            df = pd.DataFrame([{
                "sku_id": s.sku_code, "product_name": s.product_name,
                "brand": s.brand, "category": s.category,
                "subcategory": "",
                "unit_price": float(s.unit_price or 0),
                "cost_price": float(s.cost_price or 0),
                "shelf_life_days": s.shelf_life_days,
                "moq_from_supplier": s.moq_from_supplier,
                "supplier_lead_time_days": s.supplier_lead_time_days,
            } for s in skus])
            df.to_csv(os.path.join(DATA, "sku_master.csv"), index=False)
            logger.info(f"  exported {len(skus)} SKUs DB → sku_master.csv")
        outlets = Outlet.query.all()
        if outlets:
            df = pd.DataFrame([{
                "outlet_id": o.outlet_code,
                "outlet_name": "",
                "outlet_type": o.channel,
                "city": o.city,
                "area": o.area,
                "channel": o.channel,
            } for o in outlets])
            existing_path = os.path.join(DATA, "outlet_master.csv")
            if os.path.exists(existing_path):
                # preserve outlet_name from the file if present
                try:
                    existing = pd.read_csv(existing_path)
                    if "outlet_name" in existing.columns:
                        name_map = dict(zip(existing["outlet_id"], existing["outlet_name"]))
                        df["outlet_name"] = df["outlet_id"].map(name_map).fillna("")
                except Exception:
                    pass
            df.to_csv(existing_path, index=False)
            logger.info(f"  exported {len(outlets)} outlets DB → outlet_master.csv")
        invs = InventorySnapshot.query.all()
        if invs:
            df = pd.DataFrame([{
                "sku_id": SKU.query.get(i.sku_id).sku_code if SKU.query.get(i.sku_id) else "",
                "warehouse_stock": i.warehouse_stock,
                "in_transit_qty": i.in_transit_qty,
                "committed_qty": i.committed_qty,
                "last_receipt_date": i.last_receipt_date.isoformat() if i.last_receipt_date else "",
            } for i in invs])
            df = df[df["sku_id"] != ""]
            if len(df) > 0:
                df.to_csv(os.path.join(DATA, "inventory_snapshot.csv"), index=False)
                logger.info(f"  exported {len(df)} inventory snapshots DB → inventory_snapshot.csv")
    except Exception as e:
        logger.warning(f"export_db_to_csv failed (pipeline will use existing CSVs): {e}")


def get_outlet_list(store_ids=None):
    """Return all outlets scoped to user's stores."""
    q = Outlet.query
    if store_ids is not None:
        if not store_ids:
            return []
        q = q.filter(Outlet.store_id.in_(store_ids))
    return [{
        "outlet_id": o.outlet_code,
        "outlet_type": o.outlet_type or "",
        "city": o.city or "",
        "area": o.area or "",
        "channel": o.channel or "",
        "store_id": o.store_id,
    } for o in q.all()]


def get_sku_list_full(store_ids=None):
    """Return all SKU master fields as a list of dicts (DB-backed)."""
    q = _scope(SKU.query, SKU, store_ids)
    return [{
        "sku_id": s.sku_code, "product_name": s.product_name,
        "brand": s.brand, "category": s.category,
        "unit_price": float(s.unit_price or 0),
        "cost_price": float(s.cost_price or 0),
        "shelf_life_days": s.shelf_life_days,
        "moq_from_supplier": s.moq_from_supplier,
        "supplier_lead_time_days": s.supplier_lead_time_days,
        "hsn_code": s.hsn_code or "",
        "gst_rate": float(s.gst_rate or 0),
        "supplier_name": s.supplier_name or "",
        "supplier_gstin": s.supplier_gstin or "",
        "supplier_state": s.supplier_state or "",
        "is_active": bool(s.is_active),
    } for s in q.all()]


def add_audit_entry(user_name, sku_id, field, old_val, new_val, reason, store_id=None):
    """Log an inventory adjustment to the database audit table."""
    if store_id is None:
        store_id = 'store-pune-001'
    from models import DataQualityLog
    log = DataQualityLog(
        filename=f"audit:{sku_id}:{field}",
        rows_received=int(new_val),
        rows_accepted=int(old_val),
        rows_rejected=0,
        rejection_reasons={"user": user_name, "field": field,
                           "old": old_val, "new": new_val, "reason": reason},
        store_id=store_id
    )
    db.session.add(log)
    db.session.commit()
    return True


def get_audit_trail(store_ids=None):
    """Get audit trail from the database, scoped to the user's stores."""
    q = DataQualityLog.query.filter(DataQualityLog.filename.like("audit:%"))
    if store_ids is not None:
        if not store_ids:
            return []
        q = q.filter(DataQualityLog.store_id.in_(store_ids))
    logs = q.order_by(DataQualityLog.upload_date.desc()).all()
    return [{
        "timestamp": l.upload_date.isoformat() if l.upload_date else "",
        "user": l.rejection_reasons.get("user", "System") if l.rejection_reasons else "System",
        "sku_id": l.filename.split(":")[1] if ":" in l.filename else "",
        "field": l.rejection_reasons.get("field", "") if l.rejection_reasons else "",
        "old_value": l.rows_accepted,
        "new_value": l.rows_received,
        "reason": l.rejection_reasons.get("reason", "") if l.rejection_reasons else ""
    } for l in logs]


def log_barcode_scan(data, store_ids=None):
    """Log a barcode scan to the database batches table. Scan is rejected if
    the SKU does not belong to one of the user's stores."""
    sku_code = data.get("sku_code", "").strip()
    if not sku_code:
        return False, "SKU code required"
    q = SKU.query.filter_by(sku_code=sku_code)
    if store_ids is not None:
        if not store_ids:
            return False, "no store access"
        q = q.filter(SKU.store_id.in_(store_ids))
    sku = q.first()
    if not sku:
        return False, f"SKU {sku_code} not found in master"
    store_id = sku.store_id
    # Create a batch entry from the scan
    batch = Batch(
        sku_id=sku.id,
        batch_no=f"SCAN-{sku_code[-3:]}-{datetime.utcnow().strftime('%m%d%H%M')}",
        mfd_date=datetime.utcnow().date(),
        expiry_date=(datetime.utcnow() + timedelta(days=sku.shelf_life_days or 365)).date(),
        qty_received=int(data.get("qty_received", 1)),
        receipt_date=datetime.utcnow().date(),
        store_id=store_id
    )
    db.session.add(batch)
    # Update inventory snapshot
    inv = InventorySnapshot.query.filter_by(sku_id=sku.id).first()
    if inv:
        inv.warehouse_stock = (inv.warehouse_stock or 0) + int(data.get("qty_received", 1))
        inv.last_receipt_date = datetime.utcnow().date()
    db.session.commit()
    return True, f"Scan recorded: {sku_code} (+{data.get('qty_received',1)} units)"


def log_forecast_accuracy(sku_code, forecasted, actual, model_used, store_id='store-pune-001'):
    """Log forecast accuracy to the database (Phase 8 — real tracking)."""
    sku = SKU.query.filter_by(sku_code=sku_code).first()
    if not sku:
        return
    mape = abs(actual - forecasted) / max(actual, 1) * 100
    log = ForecastAccuracyLog(
        week_start_date=datetime.utcnow().date(),
        sku_id=sku.id,
        forecasted_units=forecasted,
        actual_units=actual,
        mape_contribution=round(mape, 4),
        store_id=store_id
    )
    db.session.add(log)
    db.session.commit()


def get_forecast_accuracy_from_db(store_id=None):
    """Brief Part 5B: rolling 4-week MAPE per SKU from forecast_accuracy_log.
    Falls back to the static JSON (test-set MAPE) when no actuals have been
    logged yet so dashboards remain populated on day one."""
    import numpy as np
    q = ForecastAccuracyLog.query
    if store_id:
        q = q.filter_by(store_id=store_id)
    logs = q.order_by(ForecastAccuracyLog.week_start_date.desc()).all()
    if not logs:
        json_path = os.path.join(PROCESSED, "forecast_accuracy.json")
        if os.path.exists(json_path):
            with open(json_path) as f:
                d = json.load(f)
                d["source"] = "test_set_fallback"
                d["needs_retrain"] = float(d.get("overall_mape", 0)) > 20
                return d
        return {"overall_mape_pct": 0, "per_sku_mape": {}, "source": "empty"}

    # Group by SKU, take last 4 entries each (rolling 4-week)
    per_sku = {}
    for log in logs:
        sku = SKU.query.get(log.sku_id)
        if not sku:
            continue
        per_sku.setdefault(sku.sku_code, []).append({
            "week": log.week_start_date,
            "mape": float(log.mape_contribution or 0),
            "forecasted": log.forecasted_units,
            "actual": log.actual_units,
        })

    per_sku_mape = {}
    flagged_for_retrain = []
    all_mapes = []
    for code, entries in per_sku.items():
        last4 = entries[:4]
        rolling = float(np.mean([e["mape"] for e in last4]))
        all_mapes.append(rolling)
        per_sku_mape[code] = {
            "mape": round(rolling, 1),
            "model_used": "lgbm_tuned",
            "weeks_of_history": len(entries),
            "rolling_window": len(last4),
        }
        if rolling > 25:
            flagged_for_retrain.append(code)

    overall = round(float(np.mean(all_mapes)), 1) if all_mapes else 0

    return {
        "overall_mape_pct": overall,
        "overall_mape": overall,
        "per_sku_mape": per_sku_mape,
        "lgbm_count": len(per_sku_mape),
        "rolling_avg_count": 0,
        "needs_retrain": overall > 15,
        "skus_flagged_for_priority_retrain": flagged_for_retrain,
        "total_weeks_logged": len(logs),
        "source": "database",
    }


def start_pipeline_run(store_id='store-pune-001'):
    """Brief Phase 7: create a pipeline_runs row at start; return its id."""
    run = PipelineRun(
        started_at=datetime.utcnow(),
        status=PipelineStatus.RUNNING,
        step_reached=0,
        store_id=store_id
    )
    db.session.add(run)
    db.session.commit()
    return run.id


def update_pipeline_step(run_id, step_reached):
    if not run_id:
        return
    run = PipelineRun.query.get(run_id)
    if run:
        run.step_reached = step_reached
        db.session.commit()


def finish_pipeline_run(run_id, success=True, error_message=None):
    if not run_id:
        return
    run = PipelineRun.query.get(run_id)
    if not run:
        return
    run.completed_at = datetime.utcnow()
    run.status = PipelineStatus.COMPLETE if success else PipelineStatus.FAILED
    if error_message:
        run.error_message = error_message
    db.session.commit()


def get_latest_pipeline_run(store_id=None, history=5):
    """Return the latest run + a short history (for dashboard rendering).
    history=N: include the N most recent runs as pipeline_history."""
    q = PipelineRun.query
    if store_id:
        q = q.filter_by(store_id=store_id)
    runs = q.order_by(PipelineRun.started_at.desc()).limit(max(history, 1)).all()
    if not runs:
        return {"running": False, "status": "idle", "step_reached": 0,
                "started_at": None, "completed_at": None, "error": None,
                "pipeline_history": []}
    run = runs[0]
    history_payload = []
    for r in runs:
        elapsed = None
        if r.started_at and r.completed_at:
            elapsed = round((r.completed_at - r.started_at).total_seconds(), 1)
        history_payload.append({
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            "status": r.status,
            "step_reached": r.step_reached,
            "elapsed_seconds": elapsed,
            "error": (r.error_message or "")[:300],
        })
    return {
        "running": run.status == PipelineStatus.RUNNING,
        "status": run.status,
        "step_reached": run.step_reached,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "error": run.error_message,
        "pipeline_history": history_payload,
    }


def log_data_quality(filename, rows_received, rows_accepted, rows_rejected,
                     rejection_reasons, store_id='store-pune-001'):
    """Log data quality to database (Phase 4 fix)."""
    log = DataQualityLog(
        filename=filename,
        rows_received=rows_received,
        rows_accepted=rows_accepted,
        rows_rejected=rows_rejected,
        rejection_reasons=rejection_reasons,
        store_id=store_id
    )
    db.session.add(log)
    db.session.commit()
    return log.id


def get_available_stock_with_batch_expiry(sku_code, store_id=None):
    """Phase 5 fix: Calculate available stock excluding expired batches."""
    sku = SKU.query.filter_by(sku_code=sku_code).first()
    if not sku:
        return 0
    today = datetime.utcnow().date()
    valid_batches = Batch.query.filter(
        Batch.sku_id == sku.id,
        Batch.expiry_date > today
    ).all()
    return sum(b.qty_received or 0 for b in valid_batches)


# CSV sync helpers were removed in Brief C8 fix: DB is the canonical store,
# CSVs are regenerated via export_db_to_csv() at pipeline startup.
