"""Predictor evaluation: run with  python tests/test_predictor.py  (no Docker needed).
Checks (1) no false alarms on normal data, (2) gradual incidents are predicted before the real warning."""
import os, sys, random, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'predictor'))
import logic

# load simulator math without running its main loop
src = open(os.path.join(ROOT, 'simulator', 'main.py')).read()
ns = {}
start = src.index('CYCLE_SECONDS'); end = src.index('def on_connect')
exec("import math, random, time\nSENSORS_PER_METRIC=3\n" + src[start:end], ns)

def stream(zone, metric, t_start, seconds, fault=None):
    """Yield aggregated points like the fog node: one avg per 2 s of 3 sensors x 2 s."""
    ns['faults'].clear()
    if fault: ns['faults'][(zone, metric)] = fault
    t = t_start
    while t < t_start + seconds:
        vals = [ns['reading'](zone, metric, t + k) for k in range(2) for _ in range(3)]
        avg = sum(vals) / len(vals)
        yield t + 2, avg
        t += 2

def classify(metric, v):
    w, c = logic.THRESHOLDS[metric]
    return "critical" if v >= c else "warning" if v >= w else "normal"

def run(zone, metric, t_start, seconds, fault=None):
    pts = []; streak = 0; first_alert = None; first_warn = None
    for ts, avg in stream(zone, metric, t_start, seconds, fault):
        status = classify(metric, avg)
        if status != "normal" and first_warn is None: first_warn = ts
        pts.append((ts, avg)); pts = pts[-logic.WINDOW:]
        f = logic.forecast(metric, pts, status)
        if f and f["will_breach"]:
            streak += 1
            if streak >= logic.STREAK_NEEDED and first_alert is None: first_alert = ts
        else:
            streak = 0
    return first_alert, first_warn

random.seed(7)
# 1) false positives: 40 minutes of normal operation, all zone/metric combos, random start phases
fp = 0
for z in "ABC":
    for m in logic.THRESHOLDS:
        a, w = run(z, m, 1000 + random.randint(0, 5000), 2400)
        if a is not None: fp += 1; print("FALSE POSITIVE", z, m)
print("false positives:", fp)

# 2) gradual faults: must be predicted before the real warning
ok = True
for z in "ABC":
    for m in logic.THRESHOLDS:
        t0 = 1000 + random.randint(0, 5000)
        a, w = run(z, m, t0 - 60, 140, fault=(t0, t0 + 70, 45.0))
        lead = None if (a is None or w is None) else round(w - a, 1)
        good = a is not None and (w is None or a < w)
        ok &= good
        print(f"{z} {m:11s} predicted_at=+{None if a is None else round(a-t0,1)}s  warning_at=+{None if w is None else round(w-t0,1)}s  lead={lead}s  {'OK' if good else 'FAIL'}")
print("ALL GRADUAL FAULTS PREDICTED EARLY:", ok)

# 3) sudden faults must not crash anything
a, w = run("A", "aqi", 5000, 80, fault=(5020, 5040, 3.0)); print("sudden fault: predicted", a, "warning", w)
