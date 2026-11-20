"""Pure helper logic for the stream processor (testable without Kafka/DB)."""


def rule_applies(rule, zone, metric):
    return (bool(rule.get("enabled", True))
            and rule.get("metric") == metric
            and rule.get("zone", "*") in ("*", zone))


def rule_breached(rule, value):
    threshold = float(rule["threshold"])
    return value > threshold if rule.get("op") == ">" else value < threshold
