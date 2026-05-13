# 🚨 URGENT: Fix "No Data Showing" on Deployed Site

## The Problem
Your production site shows no data because the **PostgreSQL database is empty**.

## The Solution (5 minutes)

### Step 1: Access Render Shell
1. Go to https://dashboard.render.com
2. Click on your **ledgr-web** service
3. Click the **"Shell"** tab at the top
4. Wait for the shell to connect

### Step 2: Run This Single Command

Copy and paste this into the Render shell:

```bash
python init_production_db.py && echo "SUCCESS - Database seeded!" || echo "FAILED - Check logs above"
```

### Step 3: Verify Data Loaded

Run this command to check:

```bash
python -c "from app import app; from models import SKU; app.app_context().push(); print(f'✓ SKUs loaded: {SKU.query.count()}')"
```

You should see: `✓ SKUs loaded: 150` (or similar number)

### Step 4: Refresh Your Website

Go to your deployed site and refresh the page. Data should now appear!

---

## Alternative: If Shell Access Doesn't Work

### Option A: Trigger via Manual Deploy

1. In Render dashboard, go to your **ledgr-web** service
2. Click **"Manual Deploy"** → **"Clear build cache & deploy"**
3. Wait for deployment to complete
4. The database will auto-seed on first startup

### Option B: Add Pre-Deploy Command

1. Edit your `render.yaml` file locally
2. Add this under the `web` service:

```yaml
services:
  - type: web
    name: ledgr-web
    # ... existing config ...
    preDeployCommand: python init_production_db.py
```

3. Commit and push to trigger a new deployment

---

## Verification Checklist

After running the fix, verify these:

- [ ] Can log in with demo credentials (owner@sunrise.com / sunrise2024)
- [ ] Dashboard shows SKU counts and metrics
- [ ] SKU Management page shows product list
- [ ] Forecast page shows data
- [ ] Reorder page shows recommendations

---

## Why This Happened

The production database was created but never seeded with initial data because:

1. The automatic seeding in `database.py` may have failed silently on first deploy
2. The CSV files exist in the Docker image but weren't loaded into PostgreSQL
3. No pre-deploy hook was configured to ensure seeding

The `init_production_db.py` script forces a fresh seed and provides detailed logging.

---

## Prevention

To prevent this in future deployments, the following changes have been made:

1. ✅ Added `init_production_db.py` - Manual seeding script
2. ✅ Enhanced logging in `database.py` - Better visibility of seeding process
3. ✅ Created `seed_production.sh` - Automated seeding script
4. ✅ Added verification checks - Confirms data was loaded

---

## Still Having Issues?

If data still doesn't show after running the fix:

1. **Check Render logs:**
   - Go to Render dashboard → ledgr-web → Logs tab
   - Look for errors related to database connection or seeding

2. **Verify environment variables:**
   - DATABASE_URL should be set (auto-configured by Render)
   - FLASK_ENV should be "production"
   - FLASK_SECRET_KEY should be set to a random string

3. **Check database status:**
   - Go to Render dashboard → ledgr-db
   - Ensure status is "Available"
   - Check that web service and database are in the same region

4. **Manual database check:**
   ```bash
   # In Render shell
   python -c "
   from app import app
   from database import db
   with app.app_context():
       result = db.session.execute(db.text('SELECT COUNT(*) FROM skus'))
       print(f'SKUs in database: {result.scalar()}')
   "
   ```

---

## Need Help?

If you're still stuck:

1. Check the full logs in Render dashboard
2. Look at `PRODUCTION_FIX.md` for more detailed troubleshooting
3. Verify all CSV files are present: `ls -la /app/data/`
4. Check database connectivity: `python -c "from app import app; app.app_context().push(); from database import db; print(db.engine.url)"`
