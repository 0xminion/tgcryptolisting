"""Receipt-verified Hermes delivery for durable outbox events."""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404
from collections.abc import Mapping
from typing import Any


class DeliveryError(RuntimeError):
    """Hermes did not prove delivery to the exact requested target."""


def _json_objects(raw: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    objects: list[dict[str, Any]] = []
    cursor = 0
    while cursor < len(raw):
        index = raw.find("{", cursor)
        if index < 0:
            break
        try:
            value, consumed = decoder.raw_decode(raw[index:])
        except json.JSONDecodeError:
            cursor = index + 1
            continue
        if isinstance(value, dict):
            objects.append(value)
        cursor = index + max(consumed, 1)
    return objects


def parse_receipt(raw: str, target: str) -> dict[str, Any]:
    """Validate positive platform, chat, optional thread, and message identity."""
    parts = target.split(":")
    if len(parts) not in {2, 3} or not all(parts):
        raise DeliveryError("target must bind platform and chat")
    platform, chat_id = parts[:2]
    thread_id = parts[2] if len(parts) == 3 else None

    objects = _json_objects(raw)
    if not objects:
        raise DeliveryError("Hermes did not return a JSON receipt")
    receipt = objects[-1]
    if receipt.get("success") is not True:
        raise DeliveryError("Hermes receipt did not report success")
    if str(receipt.get("platform", "")) != platform:
        raise DeliveryError("Hermes receipt platform does not match target")
    if str(receipt.get("chat_id", "")) != chat_id:
        raise DeliveryError("Hermes receipt chat does not match target")
    if thread_id is not None:
        effective_thread = receipt.get("thread_id", receipt.get("message_thread_id"))
        if str(effective_thread or "") != thread_id:
            raise DeliveryError("Hermes receipt does not bind the requested thread")
    message_id = receipt.get("message_id")
    if isinstance(message_id, bool) or not isinstance(message_id, (str, int)):
        raise DeliveryError("Hermes receipt has no valid message id")
    if not str(message_id).strip():
        raise DeliveryError("Hermes receipt has an empty message id")
    return receipt


def send_message(
    message: str,
    target: str,
    *,
    timeout_seconds: float = 30.0,
    executable: str = "hermes",
) -> Mapping[str, Any]:
    """Send via Hermes without a shell and require an exact positive receipt."""
    if not message.strip():
        raise DeliveryError("refusing to send an empty message")
    environment = os.environ.copy()
    for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        environment.pop(key, None)
    try:
        # Fixed argv, shell disabled, bounded timeout.
        result = subprocess.run(  # nosec B603
            [executable, "send", "--to", target, "--file", "-", "--json"],
            input=message,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            env=environment,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeliveryError(f"Hermes send failed: {exc}") from exc
    combined = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if result.returncode != 0:
        raise DeliveryError(
            f"Hermes send exited {result.returncode}: {combined[-1000:]}"
        )
    return parse_receipt(combined, target)
