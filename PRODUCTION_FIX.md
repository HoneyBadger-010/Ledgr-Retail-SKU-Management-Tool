# 🚨 PRODUCTION DATA FIX - No Data Showing on Deployed Site

## Problem
The deployed site shows no data because the **production PostgreSQL database is empty**.

## Root Cause
1. The production database on Render was created but never seeded with initial data
2. The automatic seeding in `database.py` only runs when the app starts, but may have failed silently
3. The CSV files in `/app/data/` exist in the Docker image but weren't loaded into PostgreSQL

## Solution - Run This on Render

### Option 1: Run Initialization Script (RECOMMENDED)

1. **Open Render Shell** for your web service:
   - Go to your Render dashboard
   - Click on your `ledgr-web` service
   - Click "Shell" tab
   - Run this command:

```bash
python init_production_db.py
```

This will:
- Check if the database is empty
- Seed it with data from CSV files
- Verify the data was loaded
- Show you sample SKUs to confirm

### Option 2: Force App Restart with Database Reset

If Option 1 doesn't work, you may need to reset the database:

1. **In Render Dashboard:**
   - Go to your PostgreSQL database service
   - Click "Info" tab
   - Copy the "Internal Database URL"

2. **Connect to database via Shell:**
```bash
# In Render web service shell
python -c "
from app import app
from database import db
with app.app_context():
    db.drop_all()
    db.create_all()
    from database import _seed_if_empty
    _seed_if_empty()
    from models import SKU
    print(f'SKUs loaded: {SKU.query.count()}')
"
```

### Option 3: Manual Database Seeding via SQL

If Python scripts fail, you can manually seed via SQL:

1. **Download your CSV files** from the repo
2. **Connect to Render PostgreSQL** using the External Database URL
3. **Run SQL COPY commands** to load data

## Verification

After running the fix, verify data is loaded:

```bash
# In Render shell
python -c "
from app import app
from models import SKU, Outlet, Store
with app.app_context():
    print(f'Stores: {Store.query.count()}')
    print(f'SKUs: {SKU.query.count()}')
    print(f'Outlets: {Outlet.query.count()}')
"
```

Expected output:
```
Stores: 1
SKUs: 150+
Outlets: 50+
```

## Prevention - Ensure Seeding on Deploy

To prevent this in the future, add a **post-deploy hook** in `render.yaml`:

```yaml
services:
  - type: web
    name: ledgr-web
    env: docker
    dockerfilePath: ./Dockerfile
    dockerContext: .
    healthCheckPath: /login
    envVars:
      - key: DATABASE_URL
        fromDatabase:
          name: ledgr-db
      # ... other env vars ...
    # Add this:
    preDeployCommand: python init_production_db.py
```

## Quick Fix Commands

Run these in Render Shell:

```bash
# 1. Check current state
python -c "from app import app; from models import SKU; app.app_context().push(); print(f'SKUs: {SKU.query.count()}')"

# 2. Force seed if empty
python init_production_db.py

# 3. Verify data loaded
python -c "from app import app; from models import SKU; app.app_context().push(); print(f'SKUs: {SKU.query.count()}')"

# 4. Restart the service
# (Do this from Render dashboard: Manual Deploy > Clear build cache & deploy)
```

## Environment Variables to Check

Make sure these are set in Render:

```bash
DATABASE_URL=postgresql://...  # Auto-set by Render
FLASK_SECRET_KEY=<random-64-char-string>
FLASK_ENV=production
LEDGR_PUBLIC_HOST=your-app.onrender.com
HIDE_DEMO_CREDENTIALS=1
```

## If Data Still Doesn't Show

1. **Check application logs** in Render dashboard
2. **Look for database connection errors**
3. **Verify DATABASE_URL** is correctly formatted
4. **Check if database is in the same region** as web service
5. **Ensure CSV files exist** in Docker image: `ls -la /app/data/`

## Contact Support

If none of these work, check:
- Render service logs: `render logs -s ledgr-web --tail`
- Database status in Render dashboard
- Network connectivity between web service and database
