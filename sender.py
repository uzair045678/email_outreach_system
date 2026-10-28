#!/usr/bin/env python3
"""
XR Outreach Event-Driven Email Sender
Runs every 30 minutes via Windows Task Scheduler.
Checks queue, sends due emails, exits immediately if nothing to do.
"""

import sys
import os
import json
import logging
import smtplib
import ssl
from datetime import datetime, date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from email.mime.base import MIMEBase
from email import encoders
from email.utils import formatdate, make_msgid
import openpyxl
import pytz

# Import config
try:
    from sender_config_local import (
        SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS,
        FROM_EMAIL, REPLY_TO, DAILY_LIMIT, BATCH_DELAY_SECONDS,
        OUTREACH_XLSX, COUNTER_FILE, LOG_FILE,
        MAX_RETRIES, RETRY_DELAY_SECONDS,
        EXCEL_LOCK_RETRIES, EXCEL_LOCK_DELAY_SECONDS,
        DRY_RUN, SEND_START_HOUR, SEND_END_HOUR
    )
except ImportError:
    print("ERROR: sender_config_local.py not found. Copy sender_config.py to sender_config_local.py and fill in credentials.")
    sys.exit(1)

# ============ LOGGING SETUP ============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ============ HELPER FUNCTIONS ============

def parse_utc(utc_str):
    """Parse UTC string from Excel (handles Z, +00:00, naive)"""
    if not utc_str:
        return None
    s = str(utc_str).strip()
    if s.endswith('Z'):
        s = s[:-1] + '+00:00'
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        # Try naive datetime
        try:
            return datetime.strptime(s, '%Y-%m-%dT%H:%M:%S')
        except:
            logger.warning(f"Could not parse UTC: {utc_str}")
            return None


def is_business_hours():
    """Check if current UTC time falls within business hours on a weekday"""
    now_utc = datetime.now(pytz.UTC)
    if now_utc.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    # Convert to a reference timezone for hour check
    # Since send_after_utc is already calculated for 9 AM local,
    # we just check if we're within a reasonable window
    return SEND_START_HOUR <= now_utc.hour < SEND_END_HOUR


def load_daily_counter():
    """Load today's sent count from JSON file"""
    try:
        with open(COUNTER_FILE, 'r') as f:
            data = json.load(f)
        if data.get('date') == str(date.today()):
            return data.get('count', 0)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return 0


def save_daily_counter(count):
    """Save today's sent count to JSON file"""
    data = {'date': str(date.today()), 'count': count}
    with open(COUNTER_FILE, 'w') as f:
        json.dump(data, f)


def get_next_business_day_9am(tz_name):
    """Get next business day 9 AM in given timezone, return as UTC"""
    tz = pytz.timezone(tz_name)
    now_local = datetime.now(tz)
    
    # Start from tomorrow
    next_day = now_local + timedelta(days=1)
    while next_day.weekday() >= 5:  # Skip weekend
        next_day += timedelta(days=1)
    
    # 9 AM local
    next_send = tz.localize(datetime(next_day.year, next_day.month, next_day.day, 9, 0))
    return next_send.astimezone(pytz.utc)


def send_email_smtp(to_email, subject, body, attachment_path=None):
    """Send email via SMTP with optional attachment"""
    msg = MIMEMultipart()
    msg['From'] = FROM_EMAIL
    msg['To'] = to_email
    msg['Subject'] = subject
    msg['Date'] = formatdate(localtime=True)
    msg['Message-ID'] = make_msgid()
    if REPLY_TO:
        msg['Reply-To'] = REPLY_TO
    
    msg.attach(MIMEText(body, 'plain', 'utf-8'))
    
    if attachment_path and os.path.exists(attachment_path):
        try:
            with open(attachment_path, 'rb') as f:
                part = MIMEApplication(f.read(), Name=os.path.basename(attachment_path))
            part['Content-Disposition'] = f'attachment; filename="{os.path.basename(attachment_path)}"'
            msg.attach(part)
            logger.info(f"Attached: {attachment_path}")
        except Exception as e:
            logger.warning(f"Could not attach {attachment_path}: {e}")
    elif attachment_path:
        logger.warning(f"Attachment not found: {attachment_path}")
    
    if DRY_RUN:
        logger.info(f"[DRY RUN] Would send to {to_email}: {subject}")
        return True
    
    # Send with retries
    for attempt in range(MAX_RETRIES):
        try:
            context = ssl.create_default_context()
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.starttls(context=context)
                server.login(SMTP_USER, SMTP_PASS)
                server.send_message(msg)
            return True
        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"SMTP auth failed: {e}")
            raise  # Don't retry auth errors
        except Exception as e:
            logger.warning(f"Send attempt {attempt + 1}/{MAX_RETRIES} failed: {e}")
            if attempt < MAX_RETRIES - 1:
                import time
                time.sleep(RETRY_DELAY_SECONDS)
    
    raise Exception(f"Failed after {MAX_RETRIES} attempts")


def load_workbook_with_retry(path):
    """Load workbook with retry for file locking"""
    for attempt in range(EXCEL_LOCK_RETRIES):
        try:
            return openpyxl.load_workbook(path)
        except PermissionError:
            if attempt < EXCEL_LOCK_RETRIES - 1:
                import time
                logger.warning(f"Excel locked, retry {attempt + 1}/{EXCEL_LOCK_RETRIES}...")
                time.sleep(EXCEL_LOCK_DELAY_SECONDS)
            else:
                raise


def save_workbook_with_retry(wb, path):
    """Save workbook with retry for file locking"""
    for attempt in range(EXCEL_LOCK_RETRIES):
        try:
            wb.save(path)
            return
        except PermissionError:
            if attempt < EXCEL_LOCK_RETRIES - 1:
                import time
                logger.warning(f"Excel locked on save, retry {attempt + 1}/{EXCEL_LOCK_RETRIES}...")
                time.sleep(EXCEL_LOCK_DELAY_SECONDS)
            else:
                raise


# ============ MAIN SENDER LOGIC ============

def main():
    logger.info("=" * 50)
    logger.info("XR Outreach Sender started")
    
    # Check business hours
    if not is_business_hours():
        logger.info("Outside business hours (weekdays 8-18 UTC). Exiting.")
        return 0
    
    # Load daily counter
    sent_today = load_daily_counter()
    remaining = DAILY_LIMIT - sent_today
    
    if remaining <= 0:
        logger.info(f"Daily limit reached ({sent_today}/{DAILY_LIMIT}). Exiting.")
        return 0
    
    logger.info(f"Daily sent: {sent_today}/{DAILY_LIMIT}, remaining: {remaining}")
    
    # Load workbook
    try:
        wb = load_workbook_with_retry(OUTREACH_XLSX)
        ws = wb['Queue']
    except Exception as e:
        logger.error(f"Failed to load outreach.xlsx: {e}")
        return 1
    
    now_utc = datetime.now(pytz.UTC)
    due_rows = []
    
    # Scan queue for due emails
    for row in range(2, ws.max_row + 1):
        try:
            status = ws.cell(row=row, column=19).value  # status
            approve = ws.cell(row=row, column=20).value  # approve
            send_after = ws.cell(row=row, column=21).value  # send_after_utc
            
            if status != 'initial' or approve != 'y' or not send_after:
                continue
            
            send_utc = parse_utc(send_after)
            if not send_utc:
                continue
            
            if send_utc <= now_utc:
                due_rows.append(row)
        except Exception as e:
            logger.warning(f"Error checking row {row}: {e}")
            continue
    
    if not due_rows:
        logger.info(f"No emails due at {now_utc.isoformat()}Z. Exiting.")
        return 0
    
    # Limit to remaining daily quota
    to_send = due_rows[:remaining]
    logger.info(f"Found {len(due_rows)} due, sending {len(to_send)} (daily limit)")
    
    sent_count = 0
    for row_num in to_send:
        try:
            # Extract email data
            company = ws.cell(row=row_num, column=2).value
            to_email = ws.cell(row=row_num, column=9).value
            subject = ws.cell(row=row_num, column=12).value
            body = ws.cell(row=row_num, column=14).value
            attachment = ws.cell(row=row_num, column=15).value
            tz = ws.cell(row=row_num, column=23).value  # recipient_tz
            send_after = ws.cell(row=row_num, column=21).value  # send_after_utc
            
            if not to_email or not subject or not body:
                logger.warning(f"Row {row_num}: Missing required fields, skipping")
                ws.cell(row=row_num, column=19, value='error')
                ws.cell(row=row_num, column=29, value='Missing required fields')
                continue
            
            # Send email
            logger.info(f"Sending to {company} ({to_email})...")
            send_email_smtp(to_email, subject, body, attachment)
            
            # Update status
            ws.cell(row=row_num, column=19, value='sent')
            ws.cell(row=row_num, column=24, value=now_utc.isoformat() + 'Z')  # sent_at
            sent_count += 1
            
            # Delay between emails
            if BATCH_DELAY_SECONDS > 0 and sent_count < len(to_send):
                import time
                time.sleep(BATCH_DELAY_SECONDS)
                
        except Exception as e:
            logger.error(f"Failed to send row {row_num}: {e}")
            ws.cell(row=row_num, column=19, value='error')
            ws.cell(row=row_num, column=29, value=str(e)[:500])
            continue
    
    # Save workbook
    try:
        save_workbook_with_retry(wb, OUTREACH_XLSX)
        logger.info(f"Saved outreach.xlsx with {sent_count} updates")
    except Exception as e:
        logger.error(f"Failed to save outreach.xlsx: {e}")
        return 1
    
    # Update daily counter
    new_total = sent_today + sent_count
    save_daily_counter(new_total)
    logger.info(f"Daily total: {new_total}/{DAILY_LIMIT}")
    logger.info("Sender completed successfully")
    
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)