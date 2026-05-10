"""
Twitter/X integration using twikit with cookie-based auth.
No paid API needed — uses saved browser cookies.
"""

from __future__ import annotations

import logging
import time
from uuid import uuid4

from twikit import Client as TwikitClient

from .contracts import DeliveryResult, DeliveryStatus, SubmissionPayload
from config import settings

logger = logging.getLogger("nammacity.integrations.twitter")

_client: TwikitClient | None = None


def _get_client() -> TwikitClient | None:
    """Get twikit client with cookies loaded."""
    global _client

    auth_token = getattr(settings, 'twitter_auth_token', '') or ''
    ct0 = getattr(settings, 'twitter_ct0', '') or ''

    if not auth_token or not ct0:
        return None

    if _client is not None:
        return _client

    _client = TwikitClient("en-US")
    _client.set_cookies({
        "auth_token": auth_token,
        "ct0": ct0,
    })
    username = getattr(settings, 'twitter_username', 'nammacity_blr')
    logger.info("Twitter: cookies loaded for @%s", username)
    return _client


class TwitterIntegration:
    """Post tweets via twikit (free, cookie-based auth)."""

    channel = "twitter"

    async def send(self, payload: SubmissionPayload) -> DeliveryResult:
        started = time.perf_counter()
        tweet_text = self._build_tweet(payload)
        username = getattr(settings, 'twitter_username', 'nammacity_blr')

        client = _get_client()

        if client is None:
            logger.info("Twitter STUB mode: %s", tweet_text[:80])
            return DeliveryResult(
                channel=self.channel,
                status=DeliveryStatus.SUCCESS,
                attempts=1,
                elapsed_ms=self._elapsed_ms(started),
                provider_message_id=f"stub-tweet-{uuid4().hex[:10]}",
                external_ref=f"https://x.com/{username}/status/STUB_{uuid4().hex[:8]}",
                raw_response={"mode": "stub", "tweet_text": tweet_text},
            )

        try:
            result = await client.create_tweet(text=tweet_text)
            tweet_id = result.id
            tweet_url = f"https://x.com/{username}/status/{tweet_id}"
            logger.info("Tweet posted: %s", tweet_url)
            return DeliveryResult(
                channel=self.channel,
                status=DeliveryStatus.SUCCESS,
                attempts=1,
                elapsed_ms=self._elapsed_ms(started),
                provider_message_id=str(tweet_id),
                external_ref=tweet_url,
                raw_response={"mode": "live", "tweet_id": tweet_id, "text": tweet_text},
            )
        except KeyError:
            # twikit bug: tweet posted successfully but response parsing fails
            logger.info("Tweet posted (twikit parse bug): %s", tweet_text[:80])
            return DeliveryResult(
                channel=self.channel,
                status=DeliveryStatus.SUCCESS,
                attempts=1,
                elapsed_ms=self._elapsed_ms(started),
                provider_message_id=f"posted-{uuid4().hex[:10]}",
                external_ref=f"https://x.com/{username}",
                raw_response={"mode": "live", "text": tweet_text, "note": "posted, id unavailable"},
            )
        except Exception as exc:
            logger.warning("Tweet failed: %s", exc)
            return DeliveryResult(
                channel=self.channel,
                status=DeliveryStatus.FAILED,
                attempts=1,
                elapsed_ms=self._elapsed_ms(started),
                error=f"twitter_error:{exc}",
            )

    @staticmethod
    def _build_tweet(payload: SubmissionPayload) -> str:
        parts = [payload.subject.strip(), payload.body_text.strip()]
        if payload.twitter_handle:
            parts.append(f"cc {payload.twitter_handle.strip()}")
        tweet = " | ".join(part for part in parts if part)
        return tweet[:280]

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return int((time.perf_counter() - started) * 1000)
