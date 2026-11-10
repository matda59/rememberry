"""Optional notification channels, all configured by env vars. Unset channels are skipped."""
import json
import os
import smtplib
import ssl
import urllib.parse
import urllib.request
from email.message import EmailMessage

TIMEOUT = 15
USER_AGENT = "Rememberry (https://github.com/matda59/rememberry, 1.0)"


def env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _post(url: str, data: bytes, headers: dict):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **headers}, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        resp.read()


def ntfy_url() -> str:
    url, topic = env("NTFY_URL"), env("NTFY_TOPIC")
    if topic:
        return (url or "https://ntfy.sh").rstrip("/") + "/" + topic
    return url


def send_ntfy(title: str, message: str):
    headers = {"Tags": "birthday"}
    if env("NTFY_TOKEN"):
        headers["Authorization"] = "Bearer " + env("NTFY_TOKEN")
    # Title goes in the query string: HTTP headers can't carry non-Latin-1 names.
    _post(ntfy_url() + "?" + urllib.parse.urlencode({"title": title}), message.encode(), headers)


def send_pushover(title: str, message: str):
    data = urllib.parse.urlencode({
        "token": env("PUSHOVER_TOKEN"), "user": env("PUSHOVER_USER"), "title": title, "message": message,
    }).encode()
    _post("https://api.pushover.net/1/messages.json", data, {"Content-Type": "application/x-www-form-urlencoded"})


def send_discord(title: str, message: str):
    data = json.dumps({"content": f"**{title}**\n{message}"}).encode()
    _post(env("DISCORD_WEBHOOK_URL"), data, {"Content-Type": "application/json"})


def send_email(title: str, message: str):
    msg = EmailMessage()
    msg["Subject"] = title
    msg["From"] = env("SMTP_FROM") or env("SMTP_USER") or "rememberry@localhost"
    msg["To"] = env("SMTP_TO")
    msg.set_content(message)
    host, security = env("SMTP_HOST"), (env("SMTP_SECURITY") or "starttls").lower()
    port = int(env("SMTP_PORT") or (465 if security == "ssl" else 587))
    if security == "ssl":
        smtp = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=ssl.create_default_context())
    else:
        smtp = smtplib.SMTP(host, port, timeout=TIMEOUT)
    with smtp:
        if security == "starttls":
            smtp.starttls(context=ssl.create_default_context())
        if env("SMTP_USER"):
            smtp.login(env("SMTP_USER"), env("SMTP_PASSWORD"))
        smtp.send_message(msg)


CHANNELS = [
    ("ntfy", lambda: bool(ntfy_url()), lambda: "topic on " + urllib.parse.urlsplit(ntfy_url()).netloc, send_ntfy),
    ("Pushover", lambda: bool(env("PUSHOVER_TOKEN") and env("PUSHOVER_USER")), lambda: "api.pushover.net", send_pushover),
    ("Discord", lambda: bool(env("DISCORD_WEBHOOK_URL")), lambda: "webhook " + urllib.parse.urlsplit(env("DISCORD_WEBHOOK_URL")).netloc, send_discord),
    ("Email", lambda: bool(env("SMTP_HOST") and env("SMTP_TO")), lambda: env("SMTP_TO") + " via " + env("SMTP_HOST"), send_email),
]


def channels():
    """[(name, configured, description)] for the settings page. Descriptions never include secrets."""
    return [(name, enabled(), describe() if enabled() else "") for name, enabled, describe, _ in CHANNELS]


def any_configured() -> bool:
    return any(enabled() for _, enabled, _, _ in CHANNELS)


def send_all(title: str, message: str):
    """Sends to every configured channel. Returns [(name, error or None)]."""
    results = []
    for name, enabled, _, send in CHANNELS:
        if not enabled():
            continue
        try:
            send(title, message)
            results.append((name, None))
        except Exception as exc:  # report every channel, don't let one failure stop the rest
            results.append((name, f"{type(exc).__name__}: {exc}"))
    return results
