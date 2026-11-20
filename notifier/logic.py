"""Pure helper logic for the notification service."""

DEFAULT_CHANNELS = {
    "critical": ["email", "sms"],
    "warning": ["email"],
    "predicted": ["email"],
    "info": [],
}
VALID_CHANNELS = {"email", "sms", "telegram"}


def channels_for(alert):
    """Rule alerts carry their own channel list; others use the defaults for their level."""
    chosen = alert.get("channels")
    if chosen:
        return [c for c in chosen if c in VALID_CHANNELS]
    return list(DEFAULT_CHANNELS.get(alert.get("level"), []))


def format_text(alert):
    return f"[{str(alert.get('level', 'info')).upper()}] {alert.get('message', '')}"
