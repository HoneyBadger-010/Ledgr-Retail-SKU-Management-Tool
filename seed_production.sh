#!/bin/bash
# seed_production.sh — Force database seeding on production deployment
# Run this on Render after deployment to ensure database has data

set -e  # Exit on error

echo "=========================================="
echo "LEDGR PRODUCTION DATABASE SEEDING"
echo "=========================================="

# Check if we're in production
if [ "$FLASK_ENV" != "production" ]; then
    echo "WARNING: FLASK_ENV is not set to 'production'"
    echo "Current FLASK_ENV: ${FLASK_ENV:-not set}"
fi

# Check if DATABASE_URL is set
if [ -z "$DATABASE_URL" ]; then
    echo "ERROR: DATABASE_URL is not set!"
    exit 1
fi

echo "Database URL: ${DATABASE_URL:0:30}..."

# Check if data files exist
echo ""
echo "Checking for CSV data files..."
if [ ! -f "data/sku_master.csv" ]; then
    echo "ERROR: data/sku_master.csv not found!"
    exit 1
fi
if [ ! -f "data/outlet_master.csv" ]; then
    echo "ERROR: data/outlet_master.csv not found!"
    exit 1
fi
if [ ! -f "data/inventory_snapshot.csv" ]; then
    echo "ERROR: data/inventory_snapshot.csv not found!"
    exit 1
fi
echo "✓ All CSV files found"

# Run the initialization script
echo ""
echo "Running database initialization..."
python init_production_db.py

# Check exit code
if [ $? -eq 0 ]; then
    echo ""
    echo "=========================================="
    echo "✓ DATABASE SEEDING SUCCESSFUL"
    echo "=========================================="
    exit 0
else
    echo ""
    echo "=========================================="
    echo "✗ DATABASE SEEDING FAILED"
    echo "=========================================="
    exit 1
fi
