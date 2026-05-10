from __future__ import annotations

import asyncio
import smtplib
import time
from email.message import EmailMessage
from uuid import uuid4

from .contracts import DeliveryResult, DeliveryStatus, SubmissionPayload

from config import settings


class GmailIntegration:
    """
    Gmail SMTP integration with user-attribution support.
    Reads credentials from config.settings (Pydantic Settings).
    Supports Cc, Reply-To, Bcc for the user-attribution model.
    """

    channel = "gmail"

    async def send(
        self,
        payload: SubmissionPayload,
        cc: str | list[str] | None = None,
        bcc: str | list[str] | None = None,
        reply_to: str | None = None,
    ) -> DeliveryResult:
        started = time.perf_counter()

        if not payload.recipients:
            return DeliveryResult(
                channel=self.channel,
                status=DeliveryStatus.SKIPPED,
                attempts=1,
                elapsed_ms=self._elapsed_ms(started),
                error="no_recipients",
            )

        username = settings.gmail_user
        password = settings.gmail_app_password

        if not username or not password:
            return DeliveryResult(
                channel=self.channel,
                status=DeliveryStatus.SUCCESS,
                attempts=1,
                elapsed_ms=self._elapsed_ms(started),
                provider_message_id=f"stub-mail-{uuid4().hex[:10]}",
                raw_response={
                    "mode": "stub",
                    "recipients": payload.recipients,
                    "cc": cc,
                    "reply_to": reply_to,
                },
            )

        try:
            message_id = await asyncio.to_thread(
                self._send_smtp, payload, username, password, cc, bcc, reply_to
            )
            return DeliveryResult(
                channel=self.channel,
                status=DeliveryStatus.SUCCESS,
                attempts=1,
                elapsed_ms=self._elapsed_ms(started),
                provider_message_id=message_id,
                raw_response={
                    "recipients": payload.recipients,
                    "cc": cc,
                    "reply_to": reply_to,
                },
            )
        except smtplib.SMTPException as exc:
            return DeliveryResult(
                channel=self.channel,
                status=DeliveryStatus.FAILED,
                attempts=1,
                elapsed_ms=self._elapsed_ms(started),
                error=f"smtp_error:{exc}",
            )
        except Exception as exc:
            return DeliveryResult(
                channel=self.channel,
                status=DeliveryStatus.FAILED,
                attempts=1,
                elapsed_ms=self._elapsed_ms(started),
                error=f"unexpected_error:{exc}",
            )

    def _send_smtp(
        self,
        payload: SubmissionPayload,
        username: str,
        password: str,
        cc: str | list[str] | None,
        bcc: str | list[str] | None,
        reply_to: str | None,
    ) -> str:
        msg = EmailMessage()
        msg["Subject"] = payload.subject
        msg["From"] = username
        msg["To"] = ", ".join(payload.recipients)
        msg["X-Correlation-ID"] = payload.correlation_id

        # User-attribution headers
        if cc:
            cc_list = [cc] if isinstance(cc, str) else cc
            msg["Cc"] = ", ".join(cc_list)
        if reply_to:
            msg["Reply-To"] = reply_to
        if bcc:
            bcc_list = [bcc] if isinstance(bcc, str) else bcc
            msg["Bcc"] = ", ".join(bcc_list)

        # Body: plain text + optional HTML
        msg.set_content(payload.body_text)
        if payload.body_html:
            msg.add_alternative(payload.body_html, subtype="html")

        # Build full recipient list for SMTP envelope
        all_recipients = list(payload.recipients)
        if cc:
            all_recipients.extend([cc] if isinstance(cc, str) else cc)
        if bcc:
            all_recipients.extend([bcc] if isinstance(bcc, str) else bcc)

        # Use SSL on port 465 (works on Railway/Docker where port 587 is blocked)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as smtp:
            smtp.login(username, password)
            smtp.sendmail(username, all_recipients, msg.as_string())

        return msg["Message-ID"] or f"smtp-{uuid4().hex[:12]}"

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return int((time.perf_counter() - started) * 1000)
