"""
notifications.py — WhatsApp, Email, and Telegram Notifications (Brief Phase 13)

Sends Monday morning pipeline reports via:
  1. Twilio WhatsApp Business API (primary)
  2. Flask-Mail email (fallback)
  3. Telegram Bot API (optional)
"""
import os
import json
import requests
from datetime import datetime


def load_report():
    """Load latest Monday report."""
    root = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(root, "data", "processed", "monday_report.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def format_whatsapp_message(report):
    """Format concise WhatsApp message from Monday report."""
    es = report.get("executive_summary", {})
    date = report.get("report_date", datetime.now().strftime("%Y-%m-%d"))

    msg = (
        f"📦 *Sunrise Monday Report [{date}]*\n\n"
        f"• *{es.get('total_skus_to_reorder', 0)}* SKUs need reorder\n"
        f"• *{es.get('skus_at_stockout_risk', 0)}* at stockout risk\n"
        f"• Total order value: *₹{es.get('total_order_value_inr', 0):,}*\n"
        f"• Revenue at risk: *₹{es.get('total_revenue_at_risk_inr', 0):,}*\n"
    )

    # Urgent items
    urgent = report.get("urgent_orders", [])[:3]
    if urgent:
        msg += "\n*Top Urgent:*\n"
        for u in urgent:
            msg += f"  ▸ {u['sku_id']} ({u['product_name']}): {u['weeks_of_stock']}w stock\n"

    # Expiry alerts
    shelf_violations = es.get("shelf_life_violations", 0)
    if shelf_violations > 0:
        msg += f"\n⚠️ *{shelf_violations} SKUs near expiry — check dashboard.*\n"

    # Dead stock
    dead = es.get("dead_stock_count", 0)
    if dead > 0:
        msg += f"\n🔴 {dead} dead stock SKUs flagged for clearance.\n"

    msg += f"\n🔗 Dashboard: http://localhost:5000/"
    return msg


def send_whatsapp(message):
    """Send via Twilio WhatsApp Business API."""
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    from_num = os.environ.get("TWILIO_WHATSAPP_FROM", "")
    to_num = os.environ.get("OWNER_WHATSAPP_TO", "")

    if not all([sid, token, from_num, to_num]):
        print("[notify] WhatsApp: Missing Twilio credentials, skipping")
        return False

    try:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
        resp = requests.post(url, auth=(sid, token), data={
            "From": f"whatsapp:{from_num}",
            "To": f"whatsapp:{to_num}",
            "Body": message
        }, timeout=15)
        if resp.status_code in [200, 201]:
            print(f"[notify] WhatsApp sent successfully to {to_num}")
            return True
        else:
            print(f"[notify] WhatsApp failed: {resp.status_code} {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"[notify] WhatsApp error: {e}")
        return False


def send_email(report):
    """Send HTML email via Flask-Mail."""
    try:
        from flask_mail import Mail, Message as MailMessage
        from flask import Flask

        mail_server = os.environ.get("MAIL_SERVER", "")
        mail_user = os.environ.get("MAIL_USERNAME", "")
        mail_pass = os.environ.get("MAIL_PASSWORD", "")
        owner_email = os.environ.get("OWNER_EMAIL", "")

        if not all([mail_server, mail_user, owner_email]):
            print("[notify] Email: Missing mail config, skipping")
            return False

        app = Flask(__name__)
        app.config.update(
            MAIL_SERVER=mail_server,
            MAIL_PORT=int(os.environ.get("MAIL_PORT", 587)),
            MAIL_USE_TLS=True,
            MAIL_USERNAME=mail_user,
            MAIL_PASSWORD=mail_pass,
            MAIL_DEFAULT_SENDER=mail_user
        )
        mail = Mail(app)

        es = report.get("executive_summary", {})
        date = report.get("report_date", datetime.now().strftime("%Y-%m-%d"))

        html = f"""
        <html><body style="font-family:Inter,sans-serif;color:#1d273b">
        <div style="background:#1a1c2e;color:#fff;padding:20px;border-radius:8px 8px 0 0">
            <h2 style="color:#4ade80;margin:0">☀ Sunrise Monday Report</h2>
            <p style="color:rgba(255,255,255,0.5);margin:4px 0 0">{date}</p>
        </div>
        <div style="padding:20px;background:#fff;border:1px solid #e6e7e9">
            <table style="width:100%;border-collapse:collapse">
                <tr><td style="padding:8px;border-bottom:1px solid #eee"><strong>SKUs to Reorder</strong></td><td style="text-align:right;font-weight:700">{es.get('total_skus_to_reorder',0)}</td></tr>
                <tr><td style="padding:8px;border-bottom:1px solid #eee"><strong>Stockout Risk</strong></td><td style="text-align:right;color:#d63939;font-weight:700">{es.get('skus_at_stockout_risk',0)} SKUs</td></tr>
                <tr><td style="padding:8px;border-bottom:1px solid #eee"><strong>Total Order Value</strong></td><td style="text-align:right;font-weight:700">₹{es.get('total_order_value_inr',0):,}</td></tr>
                <tr><td style="padding:8px;border-bottom:1px solid #eee"><strong>Revenue at Risk</strong></td><td style="text-align:right;color:#d63939;font-weight:700">₹{es.get('total_revenue_at_risk_inr',0):,}</td></tr>
                <tr><td style="padding:8px;border-bottom:1px solid #eee"><strong>Dead Stock</strong></td><td style="text-align:right">{es.get('dead_stock_count',0)} SKUs</td></tr>
                <tr><td style="padding:8px"><strong>Shelf Life Violations</strong></td><td style="text-align:right">{es.get('shelf_life_violations',0)}</td></tr>
            </table>
        </div>
        <div style="padding:16px;text-align:center;background:#f8f9fa;border-radius:0 0 8px 8px">
            <a href="http://localhost:5000/" style="background:#206bc4;color:#fff;padding:10px 24px;text-decoration:none;border-radius:6px;font-weight:600">Open Dashboard</a>
        </div>
        </body></html>
        """

        with app.app_context():
            msg = MailMessage(
                subject=f"📦 Sunrise Monday Report — {date}",
                recipients=[owner_email],
                html=html
            )
            mail.send(msg)
            print(f"[notify] Email sent to {owner_email}")
            return True
    except Exception as e:
        print(f"[notify] Email error: {e}")
        return False


def send_telegram(message):
    """Send via Telegram Bot API."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not all([bot_token, chat_id]):
        print("[notify] Telegram: Missing config, skipping")
        return False

    try:
        # Convert WhatsApp markdown to Telegram markdown
        text = message.replace("*", "**")
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }, timeout=15)
        if resp.ok:
            print(f"[notify] Telegram sent to chat {chat_id}")
            return True
        else:
            print(f"[notify] Telegram failed: {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"[notify] Telegram error: {e}")
        return False


def send_all_notifications():
    """Send notifications via all configured channels. Called after pipeline completes."""
    report = load_report()
    if not report:
        print("[notify] No report found, skipping notifications")
        return

    message = format_whatsapp_message(report)
    print(f"\n[notify] Sending notifications...")

    # Try WhatsApp first
    wa_ok = send_whatsapp(message)

    # Email fallback (or always send if configured)
    if not wa_ok:
        send_email(report)

    # Telegram (always send if configured)
    send_telegram(message)

    print("[notify] Done.")


if __name__ == "__main__":
    send_all_notifications()
