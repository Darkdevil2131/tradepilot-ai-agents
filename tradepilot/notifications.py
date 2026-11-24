from __future__ import annotations

import os
import re
import smtplib
import ssl
from email.message import EmailMessage


EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class NotificationConfigurationError(RuntimeError):
    pass


class SMTPNotificationAdapter:
    """Explicit opt-in SMTP adapter. Credentials are read only from environment variables."""

    def send(self, recipient: str, subject: str, body: str) -> dict:
        if os.getenv("TRADEPILOT_SMTP_ENABLED", "false").casefold() != "true":
            raise NotificationConfigurationError("SMTP delivery is disabled")
        if not EMAIL_PATTERN.fullmatch(recipient) or "\n" in subject or "\r" in subject:
            raise ValueError("Invalid email recipient or subject")
        host = os.getenv("TRADEPILOT_SMTP_HOST", "").strip()
        sender = os.getenv("TRADEPILOT_SMTP_SENDER", "").strip()
        username = os.getenv("TRADEPILOT_SMTP_USERNAME", "").strip()
        password = os.getenv("TRADEPILOT_SMTP_PASSWORD", "")
        port = int(os.getenv("TRADEPILOT_SMTP_PORT", "587"))
        if not host or not sender:
            raise NotificationConfigurationError("SMTP host and sender are required")
        message = EmailMessage()
        message["From"] = sender
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)
        with smtplib.SMTP(host, port, timeout=10) as client:
            client.starttls(context=ssl.create_default_context())
            if username:
                client.login(username, password)
            client.send_message(message)
        return {"status": "sent", "channel": "smtp", "recipient": recipient}
