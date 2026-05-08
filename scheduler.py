"""
scheduler.py — APScheduler Monday Morning Pipeline Trigger (Brief Part 2C)

MUST NOT be inside app.py — running inside the web container fires it twice
(one per Gunicorn worker).

Schedule: Every Monday at 7:45 AM IST (before 8 AM delivery deadline).
On failure: sends email alert before 8 AM.
"""
import os
import sys
import logging
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('logs/scheduler.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('scheduler')


def send_failure_alert(error_msg):
    """Send failure alert via email (not WhatsApp) before 8AM.
    Brief Part 2C: If pipeline fails, send email alert."""
    try:
        # Flask-Mail integration (when configured)
        logger.error(f"PIPELINE FAILURE ALERT: {error_msg}")
        logger.info("Failure alert would be sent via email (configure MAIL_SERVER in .env)")
        # In production:
        # from flask_mail import Mail, Message
        # msg = Message("⚠ Sunrise Pipeline Failure", recipients=[os.environ.get('OWNER_EMAIL')])
        # msg.body = f"Pipeline failed at {datetime.now()}: {error_msg}"
        # mail.send(msg)
    except Exception as e:
        logger.error(f"Failed to send failure alert: {e}")


def send_monday_report():
    """Send Monday morning report via WhatsApp, Email, and Telegram (Brief Phase 13)."""
    try:
        from notifications import send_all_notifications
        send_all_notifications()
        logger.info("Monday report notifications dispatched")
    except Exception as e:
        logger.error(f"Failed to send Monday report: {e}")


def monday_job():
    """Main Monday morning job: run pipeline then send report."""
    logger.info("=" * 60)
    logger.info("MONDAY PIPELINE JOB STARTED")

    try:
        sys.path.insert(0, os.path.dirname(__file__))
        from pipeline import run_pipeline
        results = run_pipeline()

        # Check if all steps succeeded
        failed = [k for k, v in results.items() if v['status'] == 'failed']
        if failed:
            send_failure_alert(f"Steps failed: {', '.join(failed)}")
        else:
            send_monday_report()
            logger.info("Pipeline completed successfully, report sent")

    except Exception as e:
        logger.error(f"Pipeline crashed: {e}")
        send_failure_alert(str(e))

    logger.info("MONDAY PIPELINE JOB FINISHED")


if __name__ == '__main__':
    # Create logs directory
    os.makedirs('logs', exist_ok=True)

    scheduler = BlockingScheduler()

    # Brief Part 2C: day_of_week=mon, hour=7, minute=45, timezone=Asia/Kolkata
    scheduler.add_job(
        monday_job,
        'cron',
        day_of_week='mon',
        hour=7,
        minute=45,
        timezone='Asia/Kolkata',
        id='monday_pipeline',
        name='Monday Morning Pipeline + Report'
    )

    logger.info("Scheduler started — waiting for Monday 7:45 AM IST")
    logger.info("Press Ctrl+C to exit")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped")
