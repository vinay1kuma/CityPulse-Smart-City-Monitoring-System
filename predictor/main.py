"""CLOUD layer - predictive analytics service.
Consumes the same aggregated telemetry as the processors (its own consumer group),
fits a short-term trend per (zone, metric) and
  * stores the latest forecast in Redis (dashboard draws it as a dashed projection)
  * emits an early-warning alert to Kafka (topic alerts.events) when a metric is
    trending towards its WARNING limit within the next 30 seconds
"""
import json
import os
import socket
import time
from collections import defaultdict, deque

import redis
from kafka import KafkaAdminClient, KafkaConsumer, KafkaProducer
from kafka.admin import NewTopic
from kafka.errors import TopicAlreadyExistsError

import logic

KAFKA = os.getenv("KAFKA_BOOTSTRAP", "kafka:9092")
IN_TOPIC = "telemetry.aggregated"
ALERT_TOPIC = "alerts.events"
API_VERSION = (2, 5, 0)
ME = socket.gethostname()[:12]
COOLDOWN = 90   # seconds between predictive alerts for the same zone+metric

ZONE_NAMES = {"A": "Srirangam", "B": "Thillai Nagar", "C": "Thuvakudi (NIT)"}
LABELS = {"temperature": ("Temperature", "C"), "aqi": ("Air quality", "AQI"),
          "traffic": ("Traffic", "%"), "power": ("Power load", "MW")}

r = redis.Redis(host=os.getenv("REDIS_HOST", "redis"), port=6379, decode_responses=True)


def ensure_topics():
    admin = KafkaAdminClient(bootstrap_servers=KAFKA, api_version=API_VERSION)
    try:
        for name, parts in ((IN_TOPIC, 3), (ALERT_TOPIC, 1)):
            try:
                admin.create_topics([NewTopic(name, num_partitions=parts, replication_factor=1)])
            except TopicAlreadyExistsError:
                pass
    finally:
        admin.close()


def connect_kafka():
    while True:
        try:
            ensure_topics()
            consumer = KafkaConsumer(
                IN_TOPIC, bootstrap_servers=KAFKA, api_version=API_VERSION,
                group_id="citypulse-predictor", auto_offset_reset="latest",
                value_deserializer=lambda b: json.loads(b))
            producer = KafkaProducer(
                bootstrap_servers=KAFKA, api_version=API_VERSION,
                value_serializer=lambda v: json.dumps(v).encode(),
                key_serializer=lambda k: k.encode(), linger_ms=20)
            return consumer, producer
        except Exception as e:
            print("Waiting for Kafka...", e, flush=True)
            time.sleep(3)


consumer, producer = connect_kafka()
print(f"Predictor {ME} started", flush=True)

history = defaultdict(lambda: deque(maxlen=logic.WINDOW))
streak = defaultdict(int)
processed = 0
last_beat = 0.0


def handle(m):
    zone, metric = m["zone"], m["metric"]
    key = (zone, metric)
    history[key].append((m["ts"], m["avg"]))
    f = logic.forecast(metric, list(history[key]), m["status"])
    if f is None:
        return
    r.hset("forecast", f"{zone}:{metric}", json.dumps(dict(f, zone=zone, metric=metric, ts=m["ts"])))

    if f["will_breach"]:
        streak[key] += 1
    else:
        streak[key] = 0
    if streak[key] >= logic.STREAK_NEEDED and r.set(f"pred_cd:{zone}:{metric}", 1, nx=True, ex=COOLDOWN):
        label, unit = LABELS[metric]
        eta = max(1, round(f["eta_warn"] or 1))
        text = (f"{ZONE_NAMES.get(zone, zone)}: {label} predicted to reach WARNING in about {eta} s "
                f"(now {f['current']:.0f} {unit}, trend {f['slope']:+.2f}/s)")
        producer.send(ALERT_TOPIC, key=zone, value={
            "ts": time.time(), "zone": zone, "metric": metric, "level": "predicted",
            "value": f["current"], "message": text, "source": "predictor"})
        producer.flush()
        print("PREDICTION:", text, flush=True)


for msg in consumer:
    try:
        handle(msg.value)
        processed += 1
        if time.time() - last_beat > 1:
            r.set(f"svc:predictor:{ME}", processed, ex=6)
            last_beat = time.time()
    except Exception as e:
        print("Error handling message:", e, flush=True)
