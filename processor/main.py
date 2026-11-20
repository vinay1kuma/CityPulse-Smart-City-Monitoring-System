"""CLOUD layer - stream processor (horizontally scalable Kafka consumer group).
For each aggregated message from the fog nodes it:
  * stores history           -> PostgreSQL (SQL, time series)
  * stores latest state      -> Redis (in-memory) and pushes it to dashboards (pub/sub)
  * detects status changes and evaluates user-defined rules (stored in MongoDB)
    and publishes alerts to Kafka (topic alerts.events) for the notifier service
"""
import json
import os
import socket
import time

import psycopg2
import redis
from kafka import KafkaAdminClient, KafkaConsumer, KafkaProducer
from kafka.admin import NewTopic
from kafka.errors import TopicAlreadyExistsError
from pymongo import MongoClient

import logic

KAFKA = os.getenv("KAFKA_BOOTSTRAP", "kafka:9092")
DATABASE_URL = os.environ["DATABASE_URL"]
TOPIC = "telemetry.aggregated"
ALERT_TOPIC = "alerts.events"
API_VERSION = (2, 5, 0)
ME = socket.gethostname()[:12]

ZONE_NAMES = {"A": "Srirangam", "B": "Thillai Nagar", "C": "Thuvakudi (NIT)"}
LABELS = {"temperature": ("Temperature", "C"), "aqi": ("Air quality", "AQI"),
          "traffic": ("Traffic", "%"), "power": ("Power load", "MW")}

r = redis.Redis(host=os.getenv("REDIS_HOST", "redis"), port=6379, decode_responses=True)
rules_col = MongoClient(os.getenv("MONGO_URL", "mongodb://mongo:27017"),
                        serverSelectionTimeoutMS=3000)["citypulse"]["rules"]


def connect_postgres():
    while True:
        try:
            conn = psycopg2.connect(DATABASE_URL)
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("""CREATE TABLE IF NOT EXISTS readings (
                    id BIGSERIAL PRIMARY KEY, ts TIMESTAMPTZ NOT NULL,
                    zone TEXT, metric TEXT, avg DOUBLE PRECISION,
                    min DOUBLE PRECISION, max DOUBLE PRECISION, status TEXT)""")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_readings_metric_ts ON readings (metric, ts)")
            return conn
        except psycopg2.Error as e:
            print("Waiting for PostgreSQL...", e, flush=True)
            time.sleep(2)


def connect_kafka():
    while True:
        try:
            admin = KafkaAdminClient(bootstrap_servers=KAFKA, api_version=API_VERSION)
            try:
                for name, parts in ((TOPIC, 3), (ALERT_TOPIC, 1)):
                    try:
                        admin.create_topics([NewTopic(name, num_partitions=parts, replication_factor=1)])
                    except TopicAlreadyExistsError:
                        pass
            finally:
                admin.close()
            consumer = KafkaConsumer(
                TOPIC, bootstrap_servers=KAFKA, api_version=API_VERSION,
                group_id="citypulse-processors", auto_offset_reset="latest",
                value_deserializer=lambda b: json.loads(b))
            producer = KafkaProducer(
                bootstrap_servers=KAFKA, api_version=API_VERSION,
                value_serializer=lambda v: json.dumps(v).encode(),
                key_serializer=lambda k: k.encode(), linger_ms=20)
            return consumer, producer
        except Exception as e:
            print("Waiting for Kafka...", e, flush=True)
            time.sleep(3)


pg = connect_postgres()
consumer, producer = connect_kafka()
print(f"Processor {ME} started, consuming {TOPIC}", flush=True)

processed = 0
last_beat = 0.0
_rules = []
_rules_loaded = 0.0


def get_rules():
    """User-defined rules from MongoDB, cached for 5 seconds."""
    global _rules, _rules_loaded
    if time.time() - _rules_loaded > 5:
        try:
            _rules = list(rules_col.find({"enabled": True}))
        except Exception as e:
            print("Could not load rules:", e, flush=True)
        _rules_loaded = time.time()
    return _rules


def emit_alert(m, level, text, **extra):
    event = {"ts": m["ts"], "zone": m["zone"], "metric": m["metric"], "level": level,
             "value": m["avg"], "message": text, "source": extra.pop("source", "fog-threshold")}
    event.update(extra)
    producer.send(ALERT_TOPIC, key=m["zone"], value=event)
    print("ALERT:", text, flush=True)


def check_status_change(m):
    zone, metric, status = m["zone"], m["metric"], m["status"]
    prev = r.get(f"status:{zone}:{metric}") or "normal"
    if status == prev:
        return
    r.set(f"status:{zone}:{metric}", status)
    label, unit = LABELS[metric]
    place = ZONE_NAMES.get(zone, zone)
    if status in ("warning", "critical"):
        emit_alert(m, status, f"{place}: {label} {status.upper()} ({m['avg']} {unit})")
    elif status == "normal":
        emit_alert(m, "info", f"{place}: {label} back to normal ({m['avg']} {unit})")


def check_rules(m):
    zone, metric = m["zone"], m["metric"]
    label, unit = LABELS[metric]
    for rule in get_rules():
        if not logic.rule_applies(rule, zone, metric):
            continue
        key = f"rule:{rule['_id']}:{zone}"
        breached = logic.rule_breached(rule, m["avg"])
        active = r.exists(key)
        if breached and not active:
            r.set(key, 1)
            text = (f"Rule '{rule['name']}': {ZONE_NAMES.get(zone, zone)} {label} "
                    f"{rule['op']} {rule['threshold']} (now {m['avg']} {unit})")
            emit_alert(m, rule.get("severity", "warning"), text, source="rule",
                       rule_name=rule["name"], channels=rule.get("channels", ["email"]))
        elif not breached and active:
            r.delete(key)


def handle(m):
    global pg
    zone, metric = m["zone"], m["metric"]

    try:
        with pg.cursor() as cur:
            cur.execute("INSERT INTO readings (ts, zone, metric, avg, min, max, status) "
                        "VALUES (to_timestamp(%s), %s, %s, %s, %s, %s, %s)",
                        (m["ts"], zone, metric, m["avg"], m["min"], m["max"], m["status"]))
    except psycopg2.Error as e:
        print("PostgreSQL error, reconnecting:", e, flush=True)
        pg = connect_postgres()

    r.hset("latest", f"{zone}:{metric}", json.dumps(m))
    r.hincrby("stats", "raw_msgs", m["raw_count"])
    r.hincrby("stats", "agg_msgs", 1)
    r.set(f"fog:{zone}", m["ts"], ex=10)
    r.set("edge:last_seen", 1, ex=10)

    check_status_change(m)
    check_rules(m)
    producer.flush()
    r.publish("events", json.dumps(dict(m, type="telemetry")))


for msg in consumer:
    try:
        handle(msg.value)
        processed += 1
        if time.time() - last_beat > 1:
            r.set(f"proc:{ME}", processed, ex=6)
            last_beat = time.time()
    except Exception as e:
        print("Error handling message:", e, flush=True)
