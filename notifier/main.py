"""CLOUD layer - notification service.
Every alert in the system (fog threshold alerts, predictive alerts, user rules) is published to
the Kafka topic alerts.events. This service consumes them and
  * stores the alert (MongoDB) and pushes it live to dashboards (Redis pub/sub)
  * sends notifications on the chosen channels (email / SMS are simulated and logged,
    Telegram is real when TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set)
"""
import json
import os
import socket
import threading
import time
import smtplib
import ssl
from email.message import EmailMessage

import redis
import requests
from kafka import KafkaAdminClient, KafkaConsumer
from kafka.admin import NewTopic
from kafka.errors import TopicAlreadyExistsError
from pymongo import MongoClient

import logic

KAFKA = os.getenv("KAFKA_BOOTSTRAP", "kafka:9092")
ALERT_TOPIC = "alerts.events"
API_VERSION = (2, 5, 0)
ME = socket.gethostname()[:12]
EMAIL_TO = os.getenv("ALERT_EMAIL", "ops@citypulse.local")
PHONE_TO = os.getenv("ALERT_PHONE", "+91-90000-00000")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USERNAME)
SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "false").lower() in ("1", "true", "yes")
MSG91_AUTHKEY = os.getenv("MSG91_AUTHKEY", "")
MSG91_TEMPLATE_ID = os.getenv("MSG91_TEMPLATE_ID", "")
MSG91_API_URL = os.getenv("MSG91_API_URL", "https://control.msg91.com/api/v5/flow")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

r = redis.Redis(host=os.getenv("REDIS_HOST", "redis"), port=6379, decode_responses=True)
db = MongoClient(os.getenv("MONGO_URL", "mongodb://mongo:27017"))["citypulse"]
alerts_col = db["alerts"]
notif_col = db["notifications"]


def connect_kafka():
    while True:
        try:
            admin = KafkaAdminClient(bootstrap_servers=KAFKA, api_version=API_VERSION)
            try:
                admin.create_topics([NewTopic(ALERT_TOPIC, num_partitions=1, replication_factor=1)])
            except TopicAlreadyExistsError:
                pass
            finally:
                admin.close()
            return KafkaConsumer(
                ALERT_TOPIC, bootstrap_servers=KAFKA, api_version=API_VERSION,
                group_id="citypulse-notifier", auto_offset_reset="latest",
                value_deserializer=lambda b: json.loads(b))
        except Exception as e:
            print("Waiting for Kafka...", e, flush=True)
            time.sleep(3)


def send_email(alert, text):
    if not (SMTP_USERNAME and SMTP_PASSWORD and SMTP_FROM and EMAIL_TO):
        return False, "email not configured (SMTP credentials/recipient missing)"
    msg = EmailMessage()
    msg["Subject"] = f"CityPulse {str(alert.get('level', 'info')).upper()} alert"
    msg["From"] = SMTP_FROM
    msg["To"] = EMAIL_TO
    msg.set_content(
        "CityPulse has detected an alert.\n\n"
        f"{text}\n\n"
        f"Zone: {alert.get('zone', 'N/A')}\n"
        f"Metric: {alert.get('metric', 'N/A')}\n"
        f"Value: {alert.get('value', 'N/A')}\n"
        f"Source: {alert.get('source', 'N/A')}\n"
    )
    context = ssl.create_default_context()
    if SMTP_USE_SSL:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=15) as server:
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)
    return True, "accepted by SMTP server"


def send_sms(alert, text):
    if not (MSG91_AUTHKEY and MSG91_TEMPLATE_ID and PHONE_TO):
        return False, "SMS not configured (MSG91 authkey/template/recipient missing)"
    # MSG91 Flow API expects an approved template/Flow ID and matching variables.
    mobile = "".join(ch for ch in PHONE_TO if ch.isdigit())
    if mobile.startswith("0"):
        mobile = "91" + mobile.lstrip("0")
    elif len(mobile) == 10:
        mobile = "91" + mobile
    payload = {
        "template_id": MSG91_TEMPLATE_ID,
        "short_url": "0",
        "realTimeResponse": "1",
        "recipients": [{
            "mobiles": mobile,
            "VAR1": str(alert.get("level", "INFO")).upper(),
            "VAR2": f"{alert.get('zone', 'N/A')} {alert.get('metric', 'N/A')}",
            "VAR3": str(alert.get("value", "N/A")),
            "VAR4": str(alert.get("message", text))[:300],
        }],
    }
    response = requests.post(
        MSG91_API_URL,
        headers={"accept": "application/json", "authkey": MSG91_AUTHKEY,
                 "content-type": "application/json"},
        json=payload, timeout=15,
    )
    try:
        data = response.json()
    except ValueError:
        data = {"response": response.text[:500]}
    if not response.ok:
        return False, f"MSG91 HTTP {response.status_code}: {str(data)[:300]}"
    if isinstance(data, dict) and (data.get("type") == "success" or data.get("status") == "success"):
        return True, f"accepted by MSG91: {str(data.get('message', data.get('request_id', 'request submitted')))[:120]}"
    return False, f"MSG91 did not confirm acceptance: {str(data)[:300]}"


def send(channel, alert):
    text = logic.format_text(alert)
    to = {"email": EMAIL_TO, "sms": PHONE_TO, "telegram": TG_CHAT or "(not configured)"}[channel]
    delivered, note = False, "unsupported channel"
    try:
        if channel == "email":
            delivered, note = send_email(alert, text)
        elif channel == "sms":
            delivered, note = send_sms(alert, text)
        elif channel == "telegram":
            if TG_TOKEN and TG_CHAT:
                resp = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                                     json={"chat_id": TG_CHAT, "text": "CityPulse " + text}, timeout=10)
                try:
                    tg_data = resp.json()
                except ValueError:
                    tg_data = {}
                delivered = resp.ok and tg_data.get("ok", False)
                note = "accepted by Telegram" if delivered else f"Telegram error {resp.status_code}: {str(tg_data)[:250]}"
            else:
                delivered, note = False, "Telegram not configured (skipped)"
    except Exception as e:
        delivered, note = False, f"{channel} send failed: {type(e).__name__}: {e}"
    notif_col.insert_one({"ts": time.time(), "channel": channel, "to": to, "text": text,
                          "level": alert.get("level"), "delivered": delivered, "note": note})
    print(f"[{channel.upper()}] to {to}: {text} ({note})", flush=True)


def handle(alert):
    doc = {k: alert[k] for k in ("ts", "zone", "metric", "level", "value", "message", "source", "rule_name")
           if k in alert}
    doc.setdefault("ts", time.time())
    alerts_col.insert_one(dict(doc))                 # copy: insert_one adds an _id to its argument
    r.publish("events", json.dumps(dict(doc, type="alert")))
    for channel in logic.channels_for(alert):
        send(channel, alert)


handled = 0


def heartbeat():
    while True:
        try:
            r.set(f"svc:notifier:{ME}", handled, ex=8)
        except Exception:
            pass
        time.sleep(2)


consumer = connect_kafka()
print(f"Notifier {ME} started, consuming {ALERT_TOPIC}", flush=True)
threading.Thread(target=heartbeat, daemon=True).start()
for msg in consumer:
    try:
        handle(msg.value)
        handled += 1
    except Exception as e:
        print("Error handling alert:", e, flush=True)
