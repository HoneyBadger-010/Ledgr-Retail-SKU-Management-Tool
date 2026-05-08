"""
models.py — SQLAlchemy ORM Models for Sunrise Demand AI
Phase 1: PostgreSQL Database Schema (Brief Part 2A)

All tables defined exactly as specified in the Master Development Brief.
Uses Flask-SQLAlchemy with UUID primary keys.
"""
import uuid
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Enum as SAEnum

db = SQLAlchemy()

def gen_uuid():
    return str(uuid.uuid4())

# ── Enums ──
class UserRole:
    OWNER = 'owner'
    MANAGER = 'manager'
    SALESMAN = 'salesman'

class DataClassification:
    TRUE_ZERO = 'true_zero'
    MISSING_DATA = 'missing_data'
    STOCKOUT_GAP = 'stockout_gap'
    UNCERTAIN_EXCLUDED = 'uncertain_excluded'
    OBSERVED = 'observed'

class UrgencyFlag:
    HIGH = 'HIGH'
    MEDIUM = 'MEDIUM'
    LOW = 'LOW'

class POStatus:
    DRAFT = 'draft'
    APPROVED = 'approved'
    RECEIVED = 'received'

class PipelineStatus:
    RUNNING = 'running'
    COMPLETE = 'complete'
    FAILED = 'failed'

class OutletChannel:
    KIRANA = 'kirana'
    SUPERMARKET = 'supermarket'
    MEDICAL = 'medical'


# ── Association Table (many-to-many: user ↔ stores) ──
user_stores = db.Table('user_stores',
    db.Column('user_id', db.String(36), db.ForeignKey('users.id'), primary_key=True),
    db.Column('store_id', db.String(36), db.ForeignKey('stores.id'), primary_key=True)
)


class User(db.Model):
    """User account — owner, manager, or salesman."""
    __tablename__ = 'users'
    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=UserRole.MANAGER)
    full_name = db.Column(db.String(255), default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Relationships
    owned_stores = db.relationship('Store', backref='owner', lazy=True)
    stores = db.relationship('Store', secondary=user_stores, backref='users', lazy=True)

    # Flask-Login integration
    @property
    def is_authenticated(self):
        return True
    @property
    def is_active(self):
        return True
    @property
    def is_anonymous(self):
        return False
    def get_id(self):
        return self.id


class Store(db.Model):
    """Multi-store support — Pune & Nashik."""
    __tablename__ = 'stores'
    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    name = db.Column(db.String(255), nullable=False)
    city = db.Column(db.String(100), nullable=False)
    owner_id = db.Column(db.String(36), db.ForeignKey('users.id'))


class SKU(db.Model):
    """SKU master with GST compliance fields."""
    __tablename__ = 'skus'
    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    sku_code = db.Column(db.String(50), nullable=False)
    product_name = db.Column(db.String(255), nullable=False)
    brand = db.Column(db.String(100))
    category = db.Column(db.String(100))
    unit_price = db.Column(db.Numeric(12, 2), default=0)
    cost_price = db.Column(db.Numeric(12, 2), default=0)
    shelf_life_days = db.Column(db.Integer, default=365)
    moq_from_supplier = db.Column(db.Integer, default=6)
    supplier_lead_time_days = db.Column(db.Integer, default=7)
    hsn_code = db.Column(db.String(20))       # GST compliance — Part 6A
    gst_rate = db.Column(db.Numeric(5, 2))    # GST compliance — Part 6A
    p80_lead_time_days = db.Column(db.Numeric(5, 1))   # computed weekly — Part 5C
    mean_lead_time_days = db.Column(db.Numeric(5, 1))  # computed weekly — Part 5C
    store_id = db.Column(db.String(36), db.ForeignKey('stores.id'))
    is_active = db.Column(db.Boolean, default=True)
    # Unique constraint per store
    __table_args__ = (db.UniqueConstraint('sku_code', 'store_id', name='_sku_store_uc'),)


class Outlet(db.Model):
    """Outlet master — 320 outlets across Pune & Nashik."""
    __tablename__ = 'outlets'
    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    outlet_code = db.Column(db.String(50), nullable=False)
    outlet_type = db.Column(db.String(50))
    city = db.Column(db.String(100))
    area = db.Column(db.String(100))
    channel = db.Column(db.String(20))  # kirana / supermarket / medical
    store_id = db.Column(db.String(36), db.ForeignKey('stores.id'))


class SalesHistory(db.Model):
    """Weekly sales data per SKU per outlet."""
    __tablename__ = 'sales_history'
    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    week_start_date = db.Column(db.Date, nullable=False)
    sku_id = db.Column(db.String(36), db.ForeignKey('skus.id'))
    outlet_id = db.Column(db.String(36), db.ForeignKey('outlets.id'))
    units_sold = db.Column(db.Integer, default=0)
    returns = db.Column(db.Integer, default=0)
    promotional_flag = db.Column(db.Boolean, default=False)
    data_classification = db.Column(db.String(30))
    store_id = db.Column(db.String(36), db.ForeignKey('stores.id'))


class InventorySnapshot(db.Model):
    """Current inventory state per SKU."""
    __tablename__ = 'inventory_snapshots'
    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    sku_id = db.Column(db.String(36), db.ForeignKey('skus.id'))
    warehouse_stock = db.Column(db.Integer, default=0)
    in_transit_qty = db.Column(db.Integer, default=0)
    committed_qty = db.Column(db.Integer, default=0)
    last_receipt_date = db.Column(db.Date)
    snapshot_date = db.Column(db.Date)
    store_id = db.Column(db.String(36), db.ForeignKey('stores.id'))


class Batch(db.Model):
    """Per-batch expiry tracking — Part 2H."""
    __tablename__ = 'batches'
    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    sku_id = db.Column(db.String(36), db.ForeignKey('skus.id'))
    batch_no = db.Column(db.String(50), nullable=False)
    mfd_date = db.Column(db.Date)
    expiry_date = db.Column(db.Date)
    qty_received = db.Column(db.Integer, default=0)
    receipt_date = db.Column(db.Date)
    store_id = db.Column(db.String(36), db.ForeignKey('stores.id'))


class ReorderRecommendation(db.Model):
    """AI-generated reorder recommendations."""
    __tablename__ = 'reorder_recommendations'
    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    run_date = db.Column(db.Date, default=datetime.utcnow)
    sku_id = db.Column(db.String(36), db.ForeignKey('skus.id'))
    recommended_qty = db.Column(db.Integer, default=0)
    urgency_flag = db.Column(db.String(10))  # HIGH / MEDIUM / LOW
    stockout_date_projected = db.Column(db.Date)
    shelf_life_constraint_applied = db.Column(db.Boolean, default=False)
    lead_time_used_days = db.Column(db.Integer)
    safety_stock_weeks = db.Column(db.Numeric(5, 2))  # dynamic — Part 3 Bug 3
    store_id = db.Column(db.String(36), db.ForeignKey('stores.id'))


class PurchaseOrder(db.Model):
    """GST-compliant purchase orders — Part 6A."""
    __tablename__ = 'purchase_orders'
    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    po_number = db.Column(db.String(30), unique=True)  # PO-YYYYMMDD-NNN
    created_date = db.Column(db.Date, default=datetime.utcnow)
    sku_id = db.Column(db.String(36), db.ForeignKey('skus.id'))
    qty_ordered = db.Column(db.Integer, default=0)
    unit_price = db.Column(db.Numeric(12, 2), default=0)
    total_value = db.Column(db.Numeric(14, 2), default=0)
    hsn_code = db.Column(db.String(20))
    supplier_name = db.Column(db.String(255))
    supplier_gstin = db.Column(db.String(20))
    cgst_rate = db.Column(db.Numeric(5, 2), default=0)
    sgst_rate = db.Column(db.Numeric(5, 2), default=0)
    igst_rate = db.Column(db.Numeric(5, 2), default=0)
    po_status = db.Column(db.String(20), default=POStatus.DRAFT)
    store_id = db.Column(db.String(36), db.ForeignKey('stores.id'))


class SupplierLeadTimeLog(db.Model):
    """Supplier lead time tracking — Part 5C."""
    __tablename__ = 'supplier_lead_time_log'
    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    sku_id = db.Column(db.String(36), db.ForeignKey('skus.id'))
    order_placed_date = db.Column(db.Date)
    expected_receipt_date = db.Column(db.Date)
    actual_receipt_date = db.Column(db.Date)  # filled when stock arrives
    store_id = db.Column(db.String(36), db.ForeignKey('stores.id'))


class ForecastAccuracyLog(db.Model):
    """Model accuracy monitoring — Part 5B."""
    __tablename__ = 'forecast_accuracy_log'
    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    week_start_date = db.Column(db.Date)
    sku_id = db.Column(db.String(36), db.ForeignKey('skus.id'))
    forecasted_units = db.Column(db.Integer, default=0)
    actual_units = db.Column(db.Integer, default=0)
    mape_contribution = db.Column(db.Numeric(8, 4))
    store_id = db.Column(db.String(36), db.ForeignKey('stores.id'))


class PipelineRun(db.Model):
    """Pipeline execution tracking."""
    __tablename__ = 'pipeline_runs'
    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)
    status = db.Column(db.String(20), default=PipelineStatus.RUNNING)
    step_reached = db.Column(db.Integer, default=0)  # 1–6
    error_message = db.Column(db.Text)
    store_id = db.Column(db.String(36), db.ForeignKey('stores.id'))


class DataQualityLog(db.Model):
    """Data quality tracking — Part 5A."""
    __tablename__ = 'data_quality_log'
    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    upload_date = db.Column(db.Date, default=datetime.utcnow)
    filename = db.Column(db.String(255))
    rows_received = db.Column(db.Integer, default=0)
    rows_accepted = db.Column(db.Integer, default=0)
    rows_rejected = db.Column(db.Integer, default=0)
    rejection_reasons = db.Column(db.JSON)  # array of {reason_code, count}
    store_id = db.Column(db.String(36), db.ForeignKey('stores.id'))
