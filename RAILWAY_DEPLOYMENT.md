# 🚂 Deploy Ledgr to Railway (FREE)

Railway is easier than Render and has a better free tier with $5 free credit monthly!

## ✅ What's Been Fixed for Railway

- ✅ Dockerfile now uses `$PORT` environment variable (Railway sets this dynamically)
- ✅ Gunicorn binds to `0.0.0.0:$PORT` instead of hardcoded `5000`
- ✅ Health check uses Railway's PORT variable
- ✅ Added `railway.toml` configuration file
- ✅ Ready for PostgreSQL connection via `${{Postgres.DATABASE_URL}}`

## 🚀 Step-by-Step Deployment

### Step 1: Create Railway Account

1. Go to [railway.app](https://railway.app)
2. Click **"Login"** → **"Login with GitHub"**
3. Authorize Railway to access your repositories
4. You get **$5 free credit per month** (no credit card required initially)

### Step 2: Create New Project

1. Click **"New Project"**
2. Select **"Deploy from GitHub repo"**
3. Choose: `HoneyBadger-010/Ledgr-Retail-SKU-Management-Tool`
4. Railway will detect the Dockerfile automatically

### Step 3: Add PostgreSQL Database

1. In your project dashboard, click **"+ New"**
2. Select **"Database"** → **"Add PostgreSQL"**
3. Railway will create a PostgreSQL database automatically
4. The database will be named something like `Postgres` (you can rename it to `ledgr-db`)

### Step 4: Configure Web Service Environment Variables

1. Click on your **web service** (the one from GitHub)
2. Go to **"Variables"** tab
3. Click **"+ New Variable"** and add these:

#### Required Variables:

**1. DATABASE_URL** (Reference the PostgreSQL service)
```
Key: DATABASE_URL
Value: ${{Postgres.DATABASE_URL}}
```
⚠️ **IMPORTANT**: Use `${{Postgres.DATABASE_URL}}` exactly as shown. Railway will automatically replace this with the actual database URL.

If you renamed your database service, use that name instead:
```
${{ledgr-db.DATABASE_URL}}
```

**2. FLASK_SECRET_KEY** (Generate a random string)
```
Key: FLASK_SECRET_KEY
Value: [paste a random 32+ character string]
```

To generate locally:
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Or use Railway's generator:
- Click **"+ New Variable"**
- Click the dice icon 🎲 to generate random value
- Name it `FLASK_SECRET_KEY`

**3. FLASK_ENV**
```
Key: FLASK_ENV
Value: production
```

**4. PYTHONUNBUFFERED**
```
Key: PYTHONUNBUFFERED
Value: 1
```

**5. HIDE_DEMO_CREDENTIALS**
```
Key: HIDE_DEMO_CREDENTIALS
Value: 1
```

#### Optional Variables (add later if needed):

**For AI Chatbot:**
```
Key: OPENROUTER_KEY
Value: [your OpenRouter API key]
```

**For Custom Demo Passwords:**
```
Key: DEMO_OWNER_PASSWORD
Value: [your secure password]

Key: DEMO_MANAGER_PASSWORD
Value: [your secure password]

Key: DEMO_SALESMAN_PASSWORD
Value: [your secure password]
```

**For Notifications (optional):**
```
TWILIO_ACCOUNT_SID
TWILIO_AUTH_TOKEN
TWILIO_WHATSAPP_FROM
OWNER_WHATSAPP_TO
MAIL_SERVER
MAIL_PORT
MAIL_USE_TLS
MAIL_USERNAME
MAIL_PASSWORD
OWNER_EMAIL
```

### Step 5: Deploy!

1. After adding all variables, Railway will **automatically deploy**
2. Watch the **"Deployments"** tab for build progress
3. Build takes ~5-10 minutes
4. Once you see "Success" with a green checkmark, it's live!

### Step 6: Get Your URL

1. Go to **"Settings"** tab of your web service
2. Scroll to **"Networking"** section
3. Click **"Generate Domain"**
4. Railway will give you a URL like: `https://ledgr-web-production-xxxx.up.railway.app`
5. Visit: `https://your-url.up.railway.app/login`

### Step 7: Test Your App

Login with demo credentials:
- **Owner**: `owner` / `owner123` (or your custom password)
- **Manager**: `manager` / `manager123`
- **Salesman**: `salesman` / `salesman123`

## 💰 Railway Free Tier

**What You Get FREE:**
- ✅ **$5 credit per month** (enough for small apps)
- ✅ **No credit card required** for trial
- ✅ **No spin-down** (app stays running 24/7)
- ✅ **Automatic HTTPS**
- ✅ **Auto-deploy on git push**
- ✅ **PostgreSQL included** in the $5 credit
- ✅ **500GB bandwidth/month**

**Estimated Usage:**
- Web service: ~$3-4/month
- PostgreSQL: ~$1-2/month
- **Total: ~$5/month (covered by free credit!)**

**After Free Credit:**
- Pay only for what you use
- ~$5-10/month for small production app
- Much cheaper than Render's $14/month minimum

## 🔧 Railway-Specific Features

### Auto-Deploy on Git Push
Every time you push to `main` branch, Railway automatically:
1. Pulls latest code
2. Rebuilds Docker image
3. Deploys new version
4. Zero-downtime deployment

### Environment Variables
Railway automatically provides:
- `PORT` - Dynamic port (your app must use this!)
- `RAILWAY_ENVIRONMENT` - "production"
- `RAILWAY_PROJECT_ID` - Your project ID
- `RAILWAY_SERVICE_NAME` - Your service name

### Database Connection
Railway's `${{Postgres.DATABASE_URL}}` format:
- Automatically updates if database credentials change
- Works across service restarts
- No need to manually copy connection strings

## 🐛 Troubleshooting

### Build Fails
**Error**: "Dockerfile not found"
- **Fix**: Make sure Dockerfile is in the root of your repo
- Check: `git ls-files | grep Dockerfile`

**Error**: "Requirements installation failed"
- **Fix**: Check requirements.txt is valid
- Try building locally: `docker build -t test .`

### App Crashes on Startup

**Error**: "SQLAlchemy cannot parse DATABASE_URL"
- **Fix**: Make sure you used `${{Postgres.DATABASE_URL}}` (with double curly braces)
- Check: Variables tab shows the reference, not the actual URL

**Error**: "Address already in use"
- **Fix**: Make sure Dockerfile uses `$PORT` variable
- Check: Gunicorn command has `--bind 0.0.0.0:$PORT`

**Error**: "Connection refused"
- **Fix**: Ensure both services are in the same project
- Check: Database is running (green status)

### Health Check Fails

**Error**: "Health check timeout"
- **Fix**: Increase timeout in railway.toml (already set to 100s)
- Check: App is actually starting (view logs)

**Error**: "404 on /login"
- **Fix**: Make sure Flask routes are working
- Check: Logs for any Python errors

### Database Connection Issues

**Error**: "Could not connect to database"
- **Fix**: Verify `DATABASE_URL` variable is set correctly
- Check: Use `${{Postgres.DATABASE_URL}}` not a hardcoded URL
- Check: Database service is running

**Error**: "SSL required"
- **Fix**: Railway PostgreSQL requires SSL by default (already handled by SQLAlchemy)

## 📊 Monitoring

### View Logs
1. Click on your service
2. Go to **"Deployments"** tab
3. Click on latest deployment
4. View real-time logs

### Check Metrics
1. Go to **"Metrics"** tab
2. See CPU, Memory, Network usage
3. Monitor costs

### Set Up Alerts
1. Go to **"Settings"** → **"Notifications"**
2. Add webhook or email for deployment failures
3. Get notified of issues

## 🔄 Updating Your App

### Automatic Updates (Recommended)
1. Make changes locally
2. Commit: `git add . && git commit -m "your changes"`
3. Push: `git push origin main`
4. Railway automatically deploys!

### Manual Deploy
1. Go to **"Deployments"** tab
2. Click **"Deploy"** button
3. Select commit to deploy

### Rollback
1. Go to **"Deployments"** tab
2. Find previous successful deployment
3. Click **"⋯"** → **"Redeploy"**

## 🎯 Next Steps After Deployment

### 1. Set Up Custom Domain (Optional)
1. Go to **"Settings"** → **"Networking"**
2. Click **"Custom Domain"**
3. Add your domain (e.g., `app.yourdomain.com`)
4. Update DNS records as shown
5. Railway handles SSL automatically

### 2. Add Background Worker (Optional)
1. Click **"+ New"** → **"Empty Service"**
2. Connect same GitHub repo
3. Set **"Start Command"**: `python scheduler.py`
4. Add same environment variables as web service
5. This runs the automated pipeline scheduler

### 3. Set Up Monitoring
- Add Sentry for error tracking
- Use Railway's built-in metrics
- Set up uptime monitoring (e.g., UptimeRobot)

### 4. Backup Database
- Railway backs up PostgreSQL automatically
- Download backups from database settings
- Consider additional backup strategy for production

## 📚 Resources

- [Railway Documentation](https://docs.railway.app)
- [Railway Discord](https://discord.gg/railway) - Very helpful community!
- [Railway Status](https://status.railway.app)
- [Pricing Calculator](https://railway.app/pricing)

## 🆘 Need Help?

**Railway is stuck building?**
- Check logs for errors
- Try redeploying: Click "⋯" → "Redeploy"

**App works locally but not on Railway?**
- Check environment variables are set
- Verify DATABASE_URL format: `${{Postgres.DATABASE_URL}}`
- Check logs for Python errors

**Still having issues?**
- Railway Discord is very responsive
- Check Railway status page
- Review deployment logs carefully

---

**Ready to deploy? Follow Steps 1-7 above!** 🚀

The key fixes for Railway:
1. ✅ Use `$PORT` instead of hardcoded `5000`
2. ✅ Use `${{Postgres.DATABASE_URL}}` for database connection
3. ✅ Everything else is automatic!
