from __future__ import annotations

import json
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any

from .config import PlatformConfig


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


class Mailer:
    def __init__(self, config: PlatformConfig) -> None:
        self.config = config
        self.config.outbox_dir.mkdir(parents=True, exist_ok=True)

    def send(self, *, to_address: str, subject: str, body: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {
            "to": to_address,
            "subject": subject,
            "body": body,
            "metadata": metadata or {},
        }
        if self.config.smtp_host and self.config.smtp_sender:
            message = EmailMessage()
            message["From"] = self.config.smtp_sender
            message["To"] = to_address
            message["Subject"] = subject
            message.set_content(body)
            with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port, timeout=30) as smtp:
                if self.config.smtp_use_tls:
                    smtp.starttls()
                if self.config.smtp_username:
                    smtp.login(self.config.smtp_username, self.config.smtp_password)
                smtp.send_message(message)
            payload["delivery"] = "smtp"
            return payload

        outbox_file = self.config.outbox_dir / f"{_now_stamp()}_{subject.lower().replace(' ', '_')}.json"
        outbox_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        payload["delivery"] = "file-outbox"
        payload["outbox_file"] = str(outbox_file)
        return payload
