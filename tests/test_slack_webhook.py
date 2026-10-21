"""Webhook failures remain useful without exposing their credential-bearing URL."""

import traceback
from unittest.mock import Mock

import pytest
import requests

from stitch_lineage.io.slack_webhook import SlackWebhookError, post_message

WEBHOOK = "https://hooks.slack.com/services/T_SYNTH/B_SYNTH/SECRET_SYNTH"


@pytest.mark.parametrize(
    ("exception", "category"),
    [
        (requests.ConnectionError, "connection failed"),
        (requests.ReadTimeout, "request timed out"),
        (requests.RequestException, "request failed"),
    ],
)
def test_transport_errors_never_expose_webhook_in_message_or_traceback(
    monkeypatch, exception, category
):
    post = Mock(
        side_effect=exception(f"failed for {WEBHOOK}; path /services/T_SYNTH/B_SYNTH/SECRET_SYNTH")
    )
    monkeypatch.setattr("stitch_lineage.io.slack_webhook.requests.post", post)
    with pytest.raises(SlackWebhookError) as caught:
        post_message(WEBHOOK, "synthetic notice", backoff=0)
    rendered = "".join(traceback.format_exception(caught.value))
    assert category in str(caught.value)
    assert WEBHOOK not in rendered
    assert "SECRET_SYNTH" not in rendered
    assert post.call_count == 3


@pytest.mark.parametrize(("status", "attempts"), [(400, 1), (403, 1), (429, 3), (500, 3)])
def test_rejected_response_bodies_cannot_echo_webhook_credentials(monkeypatch, status, attempts):
    response = requests.Response()
    response.status_code = status
    response._content = f"Proxy error for {WEBHOOK}".encode()
    post = Mock(return_value=response)
    monkeypatch.setattr("stitch_lineage.io.slack_webhook.requests.post", post)
    with pytest.raises(SlackWebhookError) as caught:
        post_message(WEBHOOK, "synthetic notice", backoff=0)
    assert f"HTTP {status}" in str(caught.value)
    assert "SECRET_SYNTH" not in str(caught.value)
    assert post.call_count == attempts


def test_success_is_sent_once(monkeypatch):
    response = requests.Response()
    response.status_code = 200
    post = Mock(return_value=response)
    monkeypatch.setattr("stitch_lineage.io.slack_webhook.requests.post", post)
    post_message(WEBHOOK, "synthetic notice", backoff=0)
    post.assert_called_once_with(WEBHOOK, json={"text": "synthetic notice"}, timeout=10.0)
