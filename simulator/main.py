"""EDGE layer - simulated IoT sensors.
Every second each virtual sensor publishes a reading over MQTT:
    topic: citypulse/<zone>/<metric>
A control topic lets the dashboard inject a fault (sudden spike) on demand.
"""
import json
import math
import os
import random
import time

import paho.mqtt.client as mqtt

MQTT_HOST = os.getenv("MQTT_HOST", "mosquitto")
SENSORS_PER_METRIC = 3
CYCLE_SECONDS = 600          # one compressed "day" (10 minutes) so the charts visibly move

ZONES = {"A": 0.0, "B": 1.7, "C": 3.4}     # zone -> phase shift
METRICS = {
    #               base  amp  noise fault_target unit
    "temperature": (31,   3,   0.4,  47,  "C"),
    "aqi":         (90,   25,  4.0,  230, "AQI"),
    "traffic":     (48,   18,  2.5,  98,  "%"),
    "power":       (62,   15,  1.2,  102, "MW"),
}

faults = {}   # (zone, metric) -> (start, end, ramp_seconds)
RAMP = {"sudden": 3.0, "gradual": 45.0}


def fault_level(zone, metric, now):
    f = faults.get((zone, metric))
    if not f:
        return 0.0
    start, end, ramp = f
    if now < end:
        return max(0.0, min(1.0, (now - start) / ramp))   # ramp up
    level_at_end = max(0.0, min(1.0, (end - start) / ramp))
    decay = (now - end) / 6.0                              # then fade over 6 s
    if decay >= 1:
        faults.pop((zone, metric), None)
        return 0.0
    return level_at_end * (1.0 - decay)


def reading(zone, metric, now):
    base, amp, noise, target, _ = METRICS[metric]
    wave = amp * math.sin(2 * math.pi * now / CYCLE_SECONDS + ZONES[zone])
    normal = base + wave + random.gauss(0, noise)
    level = fault_level(zone, metric, now)
    # during a fault the reading is pulled toward an emergency level
    value = normal * (1 - level) + (target + random.gauss(0, noise)) * level
    value = max(0.0, value)
    if metric == "traffic":
        value = min(100.0, value)
    return round(value, 2)


def on_connect(client, userdata, flags, rc):
    print("Connected to MQTT broker, rc =", rc, flush=True)
    client.subscribe("citypulse/control/fault")


def on_message(client, userdata, msg):
    try:
        d = json.loads(msg.payload)
        zone, metric = d["zone"], d["metric"]
        mode = d.get("mode", "sudden")
        if mode not in RAMP:
            mode = "sudden"
        duration = max(5, min(int(d.get("duration", 20)), 180))
        if zone in ZONES and metric in METRICS:
            now = time.time()
            faults[(zone, metric)] = (now, now + duration, RAMP[mode])
            print(f"FAULT injected: zone={zone} metric={metric} mode={mode} for {duration}s", flush=True)
    except Exception as e:
        print("Bad control message:", e, flush=True)


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

print("Sensor simulator started:", len(ZONES) * len(METRICS) * SENSORS_PER_METRIC, "virtual sensors", flush=True)
while True:
    t0 = time.time()
    for zone in ZONES:
        for metric, spec in METRICS.items():
            for i in range(SENSORS_PER_METRIC):
                payload = {
                    "sensor_id": f"{zone}-{metric}-{i + 1}",
                    "zone": zone,
                    "metric": metric,
                    "value": reading(zone, metric, t0),
                    "unit": spec[4],
                    "ts": t0,
                }
                client.publish(f"citypulse/{zone}/{metric}", json.dumps(payload))
    time.sleep(max(0.0, 1.0 - (time.time() - t0)))
