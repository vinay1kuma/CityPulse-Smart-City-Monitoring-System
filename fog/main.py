"""FOG layer - one node per city zone.
Receives raw sensor readings over MQTT, and every WINDOW seconds:
  * aggregates them (avg/min/max)  -> reduces data sent to the cloud
  * classifies status locally      -> decision made close to the data
  * forwards one summary message to Kafka (topic telemetry.aggregated)
"""
import json
import os
import threading
import time
from collections import defaultdict

import paho.mqtt.client as mqtt
from kafka import KafkaAdminClient, KafkaProducer
from kafka.admin import NewTopic
from kafka.errors import TopicAlreadyExistsError

ZONE = os.environ["ZONE"]
MQTT_HOST = os.getenv("MQTT_HOST", "mosquitto")
KAFKA = os.getenv("KAFKA_BOOTSTRAP", "kafka:9092")
WINDOW = float(os.getenv("WINDOW_SECONDS", "2"))
TOPIC = "telemetry.aggregated"
API_VERSION = (2, 5, 0)

THRESHOLDS = {            # metric -> (warning, critical)
    "temperature": (38, 42),
    "aqi": (150, 200),
    "traffic": (80, 92),
    "power": (85, 95),
}

buffer = defaultdict(list)
lock = threading.Lock()


def classify(metric, value):
    warn, crit = THRESHOLDS[metric]
    if value >= crit:
        return "critical"
    if value >= warn:
        return "warning"
    return "normal"


def make_producer():
    while True:
        try:
            admin = KafkaAdminClient(bootstrap_servers=KAFKA, api_version=API_VERSION)
            try:
                admin.create_topics([NewTopic(TOPIC, num_partitions=3, replication_factor=1)])
            except TopicAlreadyExistsError:
                pass
            finally:
                admin.close()
            return KafkaProducer(
                bootstrap_servers=KAFKA,
                api_version=API_VERSION,
                value_serializer=lambda v: json.dumps(v).encode(),
                key_serializer=lambda k: k.encode(),
                linger_ms=50,
            )
        except Exception as e:
            print("Waiting for Kafka...", e, flush=True)
            time.sleep(3)


def on_connect(client, userdata, flags, rc):
    print(f"Fog node {ZONE} connected to MQTT", flush=True)
    client.subscribe(f"citypulse/{ZONE}/+")


def on_message(client, userdata, msg):
    try:
        d = json.loads(msg.payload)
        with lock:
            buffer[d["metric"]].append(float(d["value"]))
    except Exception as e:
        print("Bad reading:", e, flush=True)


producer = make_producer()
print(f"Fog node {ZONE} connected to Kafka", flush=True)

client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message
while True:
    try:
        client.connect(MQTT_HOST, 1883, 60)
        break
    except Exception as e:
        print("Waiting for MQTT broker...", e, flush=True)
        time.sleep(2)
client.loop_start()

while True:
    time.sleep(WINDOW)
    with lock:
        window = dict(buffer)
        buffer.clear()
    now = time.time()
    for metric, values in window.items():
        if not values:
            continue
        avg = sum(values) / len(values)
        producer.send(TOPIC, key=ZONE, value={
            "zone": ZONE,
            "metric": metric,
            "avg": round(avg, 2),
            "min": round(min(values), 2),
            "max": round(max(values), 2),
            "status": classify(metric, avg),
            "raw_count": len(values),
            "ts": now,
        })
    producer.flush()
