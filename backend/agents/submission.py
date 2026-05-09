"""
Submission Agent — Multi-channel dispatch with suppression logic.
Sends complaints via Twitter, Gmail, WhatsApp in parallel.
Applies milestone-based suppression to avoid spamming BBMP.
"""

import asyncio
import logging
from datetime import datetime, timezone

from agents.base import AgentInput, AgentOutput, BaseAgent
from db.client import get_client as get_supabase
from integrations.contracts import SubmissionPayload
from integrations.gmail import GmailIntegration
from integrations.twitter import TwitterIntegration
from integrations.whatsapp import WhatsAppIntegration

logger = logging.getLogger("nammacity.agents.submission")

# Every size is a milestone — always send email for demo
MILESTONES = set(range(1, 100))

twitter_client = TwitterIntegration()
gmail_client = GmailIntegration()
whatsapp_client = WhatsAppIntegration()


def _next_milestone(current: int) -> int | None:
    """Find the next milestone above current size."""
    for m in sorted(MILESTONES):
        if m > current:
            return m
    return None


class SubmissionAgent(BaseAgent):
    """Dispatch complaints across Twitter, Gmail, WhatsApp with suppression."""

    def __init__(self) -> None:
        super().__init__(name="SubmissionAgent", description="Multi-channel complaint dispatch")

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        d = agent_input.data
        complaint_id = d.get("complaint_id", "")
        drafting = d.get("drafting", {})
        routing = d.get("routing", {})
        crowd = d.get("crowd_validation", {})
        user_email = d.get("user_email")

        is_bundled = crowd.get("is_bundled", False)
        cluster_id = crowd.get("cluster_id")
        member_count = crowd.get("member_count", 1)

        # --- Step 1: Suppression check ---
        should_send, suppression_reason = await self._check_suppression(
            is_bundled, cluster_id, member_count
        )

        if not should_send:
            return AgentOutput(
                agent_name=self.name,
                success=True,
                data={
                    "status": "suppressed",
                    "suppression_reason": suppression_reason,
                    "cluster_size_at_send": member_count,
                    "next_milestone": _next_milestone(member_count),
                    "submitted_channels": [],
                    "primary_reference": None,
                    "submitted_at": datetime.now(timezone.utc).isoformat(),
                },
            )

        # --- Step 2: Dispatch in parallel ---
        officer = routing.get("ward_officer", {}) or {}
        agency = routing.get("primary_agency", {}) or {}
        raw_email = officer.get("email") or ""
        # Skip template patterns like "jc.{zone}@bbmp.gov.in"
        officer_email = raw_email if ("@" in raw_email and "{" not in raw_email) else "halederek242@gmail.com"
        officer_phone = officer.get("phone") or "+919900000000"
        twitter_handle = routing.get("twitter_handle", "@BBMPCOMM")

        email_body = drafting.get("email_body_en", "")
        email_kn = drafting.get("email_body_kn", "")
        if email_kn:
            email_body = email_body + "<hr/><h3>ಕನ್ನಡ ಅನುವಾದ</h3>" + email_kn

        # Build payloads
        tweet_payload = SubmissionPayload(
            complaint_id=complaint_id,
            correlation_id=f"tweet-{complaint_id[:8]}",
            subject=drafting.get("tweet_text", ""),
            body_text=drafting.get("tweet_text", ""),
            twitter_handle=twitter_handle,
        )

        email_payload = SubmissionPayload(
            complaint_id=complaint_id,
            correlation_id=f"email-{complaint_id[:8]}",
            subject=drafting.get("email_subject", "Civic Complaint"),
            body_text=drafting.get("email_body_en", ""),
            body_html=email_body,
            recipients=[officer_email] if officer_email else [],
        )

        wa_payload = SubmissionPayload(
            complaint_id=complaint_id,
            correlation_id=f"wa-{complaint_id[:8]}",
            subject=drafting.get("email_subject", ""),
            body_text=drafting.get("whatsapp_text", ""),
            metadata={"whatsapp_contacts": [officer_phone] if officer_phone else []},
        )

        # Run all 3 channels in parallel
        tweet_result, email_result, wa_result = await asyncio.gather(
            twitter_client.send(tweet_payload),
            gmail_client.send(
                email_payload,
                cc=[user_email] if user_email else None,
                reply_to=user_email,
            ),
            whatsapp_client.send(wa_payload),
        )

        channels = [
            {"channel": "twitter", "status": tweet_result.status.value, "reference_id": tweet_result.external_ref or tweet_result.provider_message_id, "error": tweet_result.error, "mode": (tweet_result.raw_response or {}).get("mode", "live")},
            {"channel": "email", "status": email_result.status.value, "reference_id": email_result.provider_message_id, "error": email_result.error, "mode": (email_result.raw_response or {}).get("mode", "live")},
            {"channel": "whatsapp", "status": wa_result.status.value, "reference_id": wa_result.provider_message_id, "error": wa_result.error, "mode": (wa_result.raw_response or {}).get("mode", "live")},
        ]

        # --- Step 3: Record submissions in DB ---
        await self._record_submissions(complaint_id, cluster_id, channels)

        # --- Step 4: Update cluster notification state ---
        if is_bundled and cluster_id:
            await self._update_cluster_notification(cluster_id, member_count)

        # Determine overall status
        any_success = any(c["status"] == "success" for c in channels)
        any_fail = any(c["status"] == "failed" for c in channels)
        if any_success and any_fail:
            status = "partial_failure"
        elif any_success:
            status = "sent"
        else:
            status = "failed"

        primary_ref = tweet_result.external_ref or tweet_result.provider_message_id or email_result.provider_message_id

        return AgentOutput(
            agent_name=self.name,
            success=True,
            data={
                "status": status,
                "submitted_channels": channels,
                "primary_reference": primary_ref,
                "suppression_reason": None,
                "cluster_size_at_send": member_count,
                "next_milestone": _next_milestone(member_count),
                "submitted_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def _check_suppression(
        self, is_bundled: bool, cluster_id: str | None, member_count: int
    ) -> tuple[bool, str | None]:
        """Check if this complaint should be suppressed (not sent)."""
        if not is_bundled:
            return True, None  # standalone complaints always send

        if member_count in MILESTONES:
            # Check if we already notified at this milestone
            if cluster_id:
                try:
                    client = get_supabase()
                    result = client.table("clusters").select("last_notified_at_size").eq("id", cluster_id).limit(1).execute()
                    if result.data:
                        last_size = result.data[0].get("last_notified_at_size", 0)
                        if member_count > last_size:
                            return True, None  # new milestone crossed
                        return False, f"Already notified at size {last_size}, current {member_count}"
                except Exception as e:
                    logger.warning("Suppression check failed: %s", e)
            return True, None  # milestone hit, send

        return False, f"Joined cluster of {member_count} residents. Not a milestone ({sorted(MILESTONES)}). Next notification at {_next_milestone(member_count)}."

    async def _record_submissions(self, complaint_id: str, cluster_id: str | None, channels: list[dict]) -> None:
        """Insert submission records into DB."""
        try:
            client = get_supabase()
            rows = [
                {
                    "complaint_id": complaint_id,
                    "cluster_id": cluster_id,
                    "channel": ch["channel"],
                    "status": ch["status"],
                    "reference_id": ch.get("reference_id"),
                    "error_message": ch.get("error"),
                    "mode": ch.get("mode"),
                }
                for ch in channels
            ]
            client.table("submissions").insert(rows).execute()
        except Exception as e:
            logger.warning("Submission recording failed: %s", e)

    async def _update_cluster_notification(self, cluster_id: str, size: int) -> None:
        """Update the cluster's last notification state."""
        try:
            client = get_supabase()
            client.table("clusters").update({
                "last_notified_at_size": size,
                "last_notified_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", cluster_id).execute()
        except Exception as e:
            logger.warning("Cluster notification update failed: %s", e)
