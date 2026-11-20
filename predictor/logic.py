"""Pure forecasting logic (no I/O) so it can be unit-tested.

Short-horizon linear trend forecast: a least-squares line is fitted to the most
recent aggregated points of one (zone, metric) stream and projected HORIZON seconds ahead.
"""

HORIZON = 30.0          # seconds to look ahead
WINDOW = 10             # aggregated points kept (about 20 s at one point per 2 s)
MIN_POINTS = 6
MIN_R2 = 0.7            # the trend must be consistent, not noise
STREAK_NEEDED = 2       # consecutive positive evaluations before alerting

THRESHOLDS = {          # metric -> (warning, critical)
    "temperature": (38, 42),
    "aqi": (150, 200),
    "traffic": (80, 92),
    "power": (85, 95),
}
RANGES = {              # metric -> (ymin, ymax), used for the "meaningful change" test
    "temperature": (20, 50),
    "aqi": (40, 260),
    "traffic": (0, 100),
    "power": (30, 110),
}


def fit(points):
    """points: list of (timestamp, value). Returns (value_now, slope_per_second, r2)."""
    n = len(points)
    t_last = points[-1][0]
    xs = [p[0] - t_last for p in points]
    ys = [p[1] for p in points]
    xbar = sum(xs) / n
    ybar = sum(ys) / n
    sxx = sum((x - xbar) ** 2 for x in xs)
    if sxx == 0:
        return ybar, 0.0, 0.0
    sxy = sum((x - xbar) * (y - ybar) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = ybar - slope * xbar            # fitted value at the latest timestamp
    sst = sum((y - ybar) ** 2 for y in ys)
    ssr = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    r2 = 0.0 if sst < 1e-9 else max(0.0, 1.0 - ssr / sst)
    return intercept, slope, r2


def forecast(metric, points, status, horizon=HORIZON):
    """Returns a dict describing the trend, or None if there is not enough data."""
    if len(points) < MIN_POINTS:
        return None
    warn, crit = THRESHOLDS[metric]
    ymin, ymax = RANGES[metric]
    cur, slope, r2 = fit(points)
    pred = cur + slope * horizon
    eta_warn = None
    if slope > 0:
        eta_warn = 0.0 if cur >= warn else (warn - cur) / slope
    meaningful = (pred - cur) >= 0.05 * (ymax - ymin)
    will_breach = (status == "normal" and slope > 0 and r2 >= MIN_R2
                   and cur < warn and pred >= warn and meaningful)
    return {
        "current": round(cur, 2),
        "predicted": round(pred, 2),
        "slope": round(slope, 4),
        "r2": round(r2, 3),
        "horizon": horizon,
        "eta_warn": None if eta_warn is None else round(eta_warn, 1),
        "will_breach": bool(will_breach),
        "warn": warn,
        "crit": crit,
    }
