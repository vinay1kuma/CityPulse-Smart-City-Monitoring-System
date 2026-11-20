"""API service - REST for queries/commands, WebSocket for live push to the dashboard."""
import base64
import csv
import hashlib
import hmac
import io
import json
import os
import secrets
import socket
import time
from typing import List, Optional

import paho.mqtt.publish as mqtt_publish
import psycopg2
import redis
import redis.asyncio as aioredis
from bson import ObjectId
from bson.errors import InvalidId
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pymongo import MongoClient

app = FastAPI(title="CityPulse API")

# Admin authentication. Set strong values in .env before exposing this service.
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "CityPulse@123")
JWT_SECRET = os.getenv("JWT_SECRET", "citypulse-development-secret-change-me")
TOKEN_TTL_SECONDS = int(os.getenv("TOKEN_TTL_SECONDS", "28800"))


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _password_digest(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310000)

# Derive the configured admin password hash once; never store a plaintext copy in a DB.
_ADMIN_SALT = hashlib.sha256((ADMIN_USERNAME + ":citypulse-admin").encode()).digest()[:16]
_ADMIN_PASSWORD_HASH = _password_digest(ADMIN_PASSWORD, _ADMIN_SALT)


def create_token(username: str) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(json.dumps({"sub": username, "role": "admin", "exp": int(time.time()) + TOKEN_TTL_SECONDS}, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode("ascii")
    signature = hmac.new(JWT_SECRET.encode(), signing_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url(signature)}"


def verify_token(token: str):
    try:
        header, payload, signature = token.split(".")
        signing_input = f"{header}.{payload}".encode("ascii")
        expected = hmac.new(JWT_SECRET.encode(), signing_input, hashlib.sha256).digest()
        supplied = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        if not hmac.compare_digest(expected, supplied):
            return None
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if claims.get("role") != "admin" or int(claims.get("exp", 0)) <= int(time.time()):
            return None
        return claims
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


@app.middleware("http")
async def require_admin_auth(request: Request, call_next):
    # Only login is public; all application API endpoints require an admin JWT.
    if request.url.path.startswith("/api/") and request.url.path != "/api/auth/login":
        auth = request.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        if not token or not verify_token(token):
            return JSONResponse(status_code=401, content={"detail": "Authentication required"})
    return await call_next(request)


class LoginIn(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
def admin_login(credentials: LoginIn):
    candidate = _password_digest(credentials.password, _ADMIN_SALT)
    if (not hmac.compare_digest(credentials.username, ADMIN_USERNAME)
            or not hmac.compare_digest(candidate, _ADMIN_PASSWORD_HASH)):
        # Small delay makes repeated password guessing slightly more expensive.
        time.sleep(0.25)
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return {"access_token": create_token(ADMIN_USERNAME), "token_type": "bearer",
            "expires_in": TOKEN_TTL_SECONDS, "username": ADMIN_USERNAME, "role": "admin"}


@app.get("/api/auth/me")
def admin_me(request: Request):
    token = request.headers.get("authorization", "")[7:].strip()
    claims = verify_token(token)
    if not claims:
        raise HTTPException(status_code=401, detail="Authentication required")
    return {"username": claims["sub"], "role": claims["role"]}

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
DATABASE_URL = os.environ["DATABASE_URL"]
MQTT_HOST = os.getenv("MQTT_HOST", "mosquitto")
KAFKA = os.getenv("KAFKA_BOOTSTRAP", "kafka:9092")

r = redis.Redis(host=REDIS_HOST, port=6379, decode_responses=True)
mongo = MongoClient(os.getenv("MONGO_URL", "mongodb://mongo:27017"), serverSelectionTimeoutMS=2500)
db = mongo["citypulse"]
alerts = db["alerts"]
rules_col = db["rules"]
notifications = db["notifications"]

ZONES = {"A": "Srirangam", "B": "Thillai Nagar", "C": "Thuvakudi (NIT)"}
GEO = {"A": {"lat": 10.8628, "lon": 78.6935},
       "B": {"lat": 10.8266, "lon": 78.6832},
       "C": {"lat": 10.7589, "lon": 78.8143}}
METRICS = {
    "temperature": {"label": "Temperature", "unit": "C", "warn": 38, "crit": 42, "ymin": 20, "ymax": 50},
    "aqi": {"label": "Air quality", "unit": "AQI", "warn": 150, "crit": 200, "ymin": 40, "ymax": 260},
    "traffic": {"label": "Traffic", "unit": "%", "warn": 80, "crit": 92, "ymin": 0, "ymax": 100},
    "power": {"label": "Power load", "unit": "MW", "warn": 85, "crit": 95, "ymin": 30, "ymax": 110},
}
CHANNELS = {"email", "sms", "telegram"}


def pg_conn():
    return psycopg2.connect(DATABASE_URL)


def check_metric(metric):
    if metric not in METRICS:
        raise HTTPException(400, "Unknown metric")


def check_zone(zone):
    if zone not in ZONES:
        raise HTTPException(400, "Unknown zone")


# ------------------------------------------------------------------ config / live state
@app.get("/api/config")
def config():
    return {"zones": ZONES, "metrics": METRICS, "geo": GEO}


@app.get("/api/state")
def state():
    return [json.loads(v) for v in r.hgetall("latest").values()]


@app.get("/api/forecast")
def forecast():
    return [json.loads(v) for v in r.hgetall("forecast").values()]


@app.get("/api/history")
def history(metric: str, minutes: int = 3):
    check_metric(metric)
    minutes = max(1, min(minutes, 60))
    conn = pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT zone, EXTRACT(EPOCH FROM ts), avg FROM readings "
                        "WHERE metric = %s AND ts > now() - make_interval(mins => %s) ORDER BY ts",
                        (metric, minutes))
            out = {z: [] for z in ZONES}
            for zone, t, v in cur.fetchall():
                out.setdefault(zone, []).append({"t": float(t), "v": float(v)})
            return out
    finally:
        conn.close()


@app.get("/api/alerts")
def get_alerts(limit: int = 30, zone: Optional[str] = None):
    query = {"zone": zone} if zone else {}
    docs = alerts.find(query, {"_id": 0}).sort("ts", -1).limit(max(1, min(limit, 200)))
    return list(docs)


@app.get("/api/notifications")
def get_notifications(limit: int = 30):
    docs = notifications.find({}, {"_id": 0}).sort("ts", -1).limit(max(1, min(limit, 200)))
    return list(docs)


@app.get("/api/stats")
def stats():
    raw = int(r.hget("stats", "raw_msgs") or 0)
    agg = int(r.hget("stats", "agg_msgs") or 0)
    processors = [{"id": k.split(":", 1)[1], "processed": int(r.get(k) or 0)}
                  for k in r.scan_iter("proc:*")]
    return {
        "raw_msgs": raw,
        "agg_msgs": agg,
        "reduction_pct": round(100 * (1 - agg / raw), 1) if raw else 0,
        "processors": sorted(processors, key=lambda p: p["id"]),
        "fog": {z: bool(r.exists(f"fog:{z}")) for z in ZONES},
        "alert_count": alerts.count_documents({}),
        "notification_count": notifications.count_documents({}),
    }


# ------------------------------------------------------------------ analytics (PostgreSQL history)
@app.get("/api/analytics/series")
def analytics_series(metric: str, minutes: int = 60, bucket: Optional[int] = None):
    """Time-bucketed avg/min/max per zone for one metric."""
    check_metric(metric)
    minutes = max(1, min(minutes, 1440))
    if bucket is None:
        bucket = max(2, int(minutes * 60 / 120))
    bucket = max(2, min(int(bucket), 3600))
    conn = pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT zone, floor(EXTRACT(EPOCH FROM ts) / %s) * %s AS b, "
                "AVG(avg), MIN(min), MAX(max) FROM readings "
                "WHERE metric = %s AND ts > now() - make_interval(mins => %s) "
                "GROUP BY zone, b ORDER BY b", (bucket, bucket, metric, minutes))
            out = {z: [] for z in ZONES}
            for zone, b, a, lo, hi in cur.fetchall():
                out.setdefault(zone, []).append(
                    {"t": float(b), "avg": float(a), "min": float(lo), "max": float(hi)})
            return {"bucket": bucket, "minutes": minutes, "series": out}
    finally:
        conn.close()


@app.get("/api/analytics/summary")
def analytics_summary(minutes: int = 60):
    """Per zone and metric: avg/min/max and the share of time spent in warning/critical."""
    minutes = max(1, min(minutes, 1440))
    conn = pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT zone, metric, AVG(avg), MIN(min), MAX(max), COUNT(*), "
                "100.0 * AVG((status = 'warning')::int), 100.0 * AVG((status = 'critical')::int) "
                "FROM readings WHERE ts > now() - make_interval(mins => %s) "
                "GROUP BY zone, metric ORDER BY zone, metric", (minutes,))
            return [{"zone": z, "metric": m, "avg": float(a), "min": float(lo), "max": float(hi),
                     "samples": int(n), "pct_warning": float(pw), "pct_critical": float(pc)}
                    for z, m, a, lo, hi, n, pw, pc in cur.fetchall()]
    finally:
        conn.close()


@app.get("/api/analytics/export.csv")
def analytics_export(metric: Optional[str] = None, zone: Optional[str] = None, minutes: int = 60):
    if metric:
        check_metric(metric)
    if zone:
        check_zone(zone)
    minutes = max(1, min(minutes, 1440))
    sql = ("SELECT to_char(ts AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'), zone, metric, avg, min, max, status "
           "FROM readings WHERE ts > now() - make_interval(mins => %s)")
    params = [minutes]
    if metric:
        sql += " AND metric = %s"
        params.append(metric)
    if zone:
        sql += " AND zone = %s"
        params.append(zone)
    sql += " ORDER BY ts LIMIT 200000"
    conn = pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["timestamp_utc", "zone", "metric", "avg", "min", "max", "status"])
            for row in cur.fetchall():
                w.writerow(row)
    finally:
        conn.close()
    name = f"citypulse_{metric or 'all'}_{zone or 'all'}_{minutes}min.csv"
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ------------------------------------------------------------------ alert rules (MongoDB)
class RuleIn(BaseModel):
    name: str
    zone: str = "*"
    metric: str
    op: str
    threshold: float
    severity: str = "warning"
    channels: List[str] = ["email"]


class RulePatch(BaseModel):
    enabled: bool


def rule_out(d):
    return {"id": str(d["_id"]), "name": d["name"], "zone": d["zone"], "metric": d["metric"],
            "op": d["op"], "threshold": d["threshold"], "severity": d["severity"],
            "channels": d["channels"], "enabled": d.get("enabled", True)}


def rule_id(value):
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(404, "Rule not found")


@app.get("/api/rules")
def list_rules():
    return [rule_out(d) for d in rules_col.find({}).sort("created", 1)]


@app.post("/api/rules")
def create_rule(rule: RuleIn):
    name = rule.name.strip()
    if not 1 <= len(name) <= 60:
        raise HTTPException(400, "Name must be 1-60 characters")
    if rule.zone != "*":
        check_zone(rule.zone)
    check_metric(rule.metric)
    if rule.op not in (">", "<"):
        raise HTTPException(400, "Operator must be > or <")
    if rule.severity not in ("warning", "critical"):
        raise HTTPException(400, "Severity must be warning or critical")
    channels = [c for c in rule.channels if c in CHANNELS]
    if not channels:
        raise HTTPException(400, "Choose at least one channel (email, sms, telegram)")
    doc = {"name": name, "zone": rule.zone, "metric": rule.metric, "op": rule.op,
           "threshold": rule.threshold, "severity": rule.severity, "channels": channels,
           "enabled": True, "created": time.time()}
    rules_col.insert_one(doc)
    return rule_out(doc)


@app.patch("/api/rules/{rid}")
def toggle_rule(rid: str, patch: RulePatch):
    res = rules_col.update_one({"_id": rule_id(rid)}, {"$set": {"enabled": patch.enabled}})
    if res.matched_count == 0:
        raise HTTPException(404, "Rule not found")
    return {"id": rid, "enabled": patch.enabled}


@app.delete("/api/rules/{rid}")
def delete_rule(rid: str):
    res = rules_col.delete_one({"_id": rule_id(rid)})
    if res.deleted_count == 0:
        raise HTTPException(404, "Rule not found")
    return {"id": rid, "deleted": True}


# ------------------------------------------------------------------ operations / health
def tcp_ok(host, port):
    try:
        socket.create_connection((host, port), timeout=1).close()
        return True
    except OSError:
        return False


def safe(fn, default=False):
    try:
        return fn()
    except Exception:
        return default


def pg_ok():
    conn = pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        return True
    finally:
        conn.close()


def count_keys(pattern):
    return len(list(r.scan_iter(pattern)))


@app.get("/api/health")
def health():
    items = []

    def add(name, layer, ok, detail=""):
        items.append({"name": name, "layer": layer, "status": "ok" if ok else "down", "detail": detail})

    edge = safe(lambda: bool(r.exists("edge:last_seen")))
    add("Sensor simulator (36 virtual sensors)", "edge", edge, "streaming" if edge else "no data")
    add("MQTT broker (Mosquitto)", "edge", tcp_ok(MQTT_HOST, 1883))
    for z, name in ZONES.items():
        add(f"Fog node {z}", "fog", safe(lambda z=z: bool(r.exists(f"fog:{z}"))), name)
    kh, kp = KAFKA.split(":")
    add("Kafka broker", "cloud", tcp_ok(kh, int(kp)))
    n = safe(lambda: count_keys("proc:*"), 0)
    add("Stream processors", "cloud", n > 0, f"{n} running")
    n = safe(lambda: count_keys("svc:predictor:*"), 0)
    add("Predictor service", "cloud", n > 0, f"{n} running")
    n = safe(lambda: count_keys("svc:notifier:*"), 0)
    add("Notifier service", "cloud", n > 0, f"{n} running")
    add("API service", "cloud", True, "this service")
    add("Redis (state + pub/sub)", "data", safe(lambda: r.ping()))
    add("PostgreSQL (history)", "data", safe(pg_ok))
    add("MongoDB (alerts, rules)", "data", safe(lambda: mongo.admin.command("ping")["ok"] == 1))
    return items


_kafka = {"admin": None, "consumer": None}
GROUPS = {"citypulse-processors": "telemetry.aggregated",
          "citypulse-predictor": "telemetry.aggregated",
          "citypulse-notifier": "alerts.events"}


def kafka_clients():
    from kafka import KafkaAdminClient, KafkaConsumer
    if _kafka["admin"] is None:
        _kafka["admin"] = KafkaAdminClient(bootstrap_servers=KAFKA, api_version=(2, 5, 0),
                                           request_timeout_ms=6000)
    if _kafka["consumer"] is None:
        _kafka["consumer"] = KafkaConsumer(bootstrap_servers=KAFKA, api_version=(2, 5, 0),
                                           session_timeout_ms=3000, heartbeat_interval_ms=1000,
                                           request_timeout_ms=6000)
    return _kafka["admin"], _kafka["consumer"]


@app.get("/api/kafka")
def kafka_info():
    """Topics, partitions and consumer lag (how far each consumer group is behind)."""
    cached = safe(lambda: r.get("cache:kafka"), None)
    if cached:
        return json.loads(cached)
    try:
        from kafka import TopicPartition
        admin, consumer = kafka_clients()
        consumer.topics()
        ends, topics = {}, []
        for t in ("telemetry.aggregated", "alerts.events"):
            parts = sorted(consumer.partitions_for_topic(t) or [])
            tps = [TopicPartition(t, p) for p in parts]
            e = consumer.end_offsets(tps) if tps else {}
            ends.update(e)
            topics.append({"topic": t, "partitions": len(tps), "messages": int(sum(e.values()))})
        groups = []
        for g, t in GROUPS.items():
            offsets = admin.list_consumer_group_offsets(g)
            lag, found = 0, False
            for tp, om in offsets.items():
                if tp.topic == t and tp in ends:
                    found = True
                    lag += max(0, ends[tp] - om.offset)
            groups.append({"group": g, "topic": t, "lag": lag if found else None})
        out = {"ok": True, "topics": topics, "groups": groups}
    except Exception as e:
        _kafka["admin"] = _kafka["consumer"] = None
        out = {"ok": False, "error": str(e)[:160]}
    safe(lambda: r.set("cache:kafka", json.dumps(out), ex=4))
    return out


# ------------------------------------------------------------------ fault injection + live push
class Fault(BaseModel):
    zone: str
    metric: str
    mode: str = "sudden"
    duration: Optional[int] = None


@app.post("/api/fault")
def inject_fault(f: Fault):
    check_zone(f.zone)
    check_metric(f.metric)
    if f.mode not in ("sudden", "gradual"):
        raise HTTPException(400, "Mode must be sudden or gradual")
    duration = f.duration or (70 if f.mode == "gradual" else 20)
    payload = {"zone": f.zone, "metric": f.metric, "mode": f.mode, "duration": duration}
    mqtt_publish.single("citypulse/control/fault", json.dumps(payload), hostname=MQTT_HOST)
    return {"status": "fault injected", **payload}


@app.websocket("/ws")
async def live(ws: WebSocket):
    token = ws.query_params.get("token", "")
    if not token or not verify_token(token):
        await ws.close(code=1008, reason="Authentication required")
        return
    await ws.accept()
    conn = aioredis.Redis(host=REDIS_HOST, port=6379, decode_responses=True)
    pubsub = conn.pubsub()
    await pubsub.subscribe("events")
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                await ws.send_text(message["data"])
    except Exception:
        pass
    finally:
        await pubsub.unsubscribe("events")
        await conn.aclose()
