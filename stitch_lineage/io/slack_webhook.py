"""Posting to a Slack incoming webhook (SPEC.md section 14).

The second file in the codebase allowed to import requests, and for the same reason as the
first: HTTP lives at the edge, behind a named client, so everything above it is testable
without a network. It knows one verb -- post this text to this URL -- and holds no state.

Deliberately not a Slack API client: no tokens, no channels, no message updating, no
interactive callbacks. A webhook URL is the whole configuration surface, and the bot name,
avatar and destination channel belong to the Slack app that issued it (issue #143 rules a
hosted approval service out of scope, and this is what keeps that true).
"""

import time
from typing import Any

import requests

_RETRY_ATTEMPTS = 3
_MAX_BLOCK_CHARS = 2900


class SlackWebhookError(Exception):
    """The webhook rejected the message, or could not be reached."""


def post_message(
    webhook_url: str, text: str, *, timeout: float = 10.0, backoff: float = 0.5
) -> None:
    """Post `text` as Slack mrkdwn. Raises SlackWebhookError on failure.

    Retries transport failures, 429 and 5xx. HTTP successes and other 4xx responses
    are never retried. A transport failure can be ambiguous, so retries do not promise
    exactly-once delivery. Errors expose only safe categories/status codes: exception
    messages and response bodies can contain the credential-bearing webhook URL.
    """
    body: dict[str, Any] = {"text": text}
    last_error = ""
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            response = requests.post(webhook_url, json=body, timeout=timeout)
        except requests.RequestException as exc:
            if isinstance(exc, requests.Timeout):
                last_error = "request timed out"
            elif isinstance(exc, requests.ConnectionError):
                last_error = "connection failed"
            else:
                last_error = "request failed"
        else:
            if response.ok:
                return
            last_error = f"HTTP {response.status_code}"
            if response.status_code != 429 and response.status_code < 500:
                raise SlackWebhookError(f"Slack webhook rejected the message -- {last_error}")
        if attempt < _RETRY_ATTEMPTS - 1 and backoff:
            time.sleep(backoff * 2**attempt)
    raise SlackWebhookError(
        f"could not post to the Slack webhook after {_RETRY_ATTEMPTS} attempts -- {last_error}"
    )


def split_message(text: str, limit: int = _MAX_BLOCK_CHARS) -> list[str]:
    """Split a long notice on line boundaries so Slack does not truncate it.

    Slack silently drops the tail of an over-long message, and a truncated mend notice
    would hide exactly the cards at the bottom of the list. Splitting on newlines keeps
    each chunk readable on its own; a single line longer than the limit is left intact
    rather than cut mid-word.
    """
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for line in text.splitlines():
        addition = len(line) + 1
        if current and length + addition > limit:
            chunks.append("\n".join(current))
            current, length = [], 0
        current.append(line)
        length += addition
    if current:
        chunks.append("\n".join(current))
    return chunks or [text]
