import pytest

from listing_tracker.live_delivery import DeliveryError, parse_receipt, send_message


def test_receipt_requires_destination_bound_success():
    raw = 'warning\n{"success":true,"platform":"telegram","chat_id":"123456789","message_id":"23717"}'
    receipt = parse_receipt(raw, "telegram:123456789")
    assert receipt["message_id"] == "23717"


def test_nested_receipt_fields_do_not_replace_the_top_level_receipt():
    raw = (
        '{"success":true,"platform":"telegram","chat_id":"123456789",'
        '"message_id":"23717","metadata":{"attempt":1}}'
    )
    assert parse_receipt(raw, "telegram:123456789")["message_id"] == "23717"


def test_receipt_rejects_wrong_chat():
    raw = '{"success":true,"platform":"telegram","chat_id":"999","message_id":"1"}'
    with pytest.raises(DeliveryError, match="chat"):
        parse_receipt(raw, "telegram:123456789")


def test_thread_target_fails_closed_when_receipt_does_not_bind_thread():
    raw = (
        '{"success":true,"platform":"telegram","chat_id":"123456789","message_id":"1"}'
    )
    with pytest.raises(DeliveryError, match="thread"):
        parse_receipt(raw, "telegram:123456789:777")


def test_thread_target_accepts_exact_effective_thread():
    raw = '{"success":true,"platform":"telegram","chat_id":"123456789","thread_id":"777","message_id":1}'
    assert parse_receipt(raw, "telegram:123456789:777")["message_id"] == 1


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        '{"success":false,"platform":"telegram","chat_id":"123456789","message_id":"1"}',
        '{"success":true,"platform":"telegram","chat_id":"123456789"}',
    ],
)
def test_receipt_rejects_non_positive_or_malformed_results(raw):
    with pytest.raises(DeliveryError):
        parse_receipt(raw, "telegram:123456789")


def test_send_message_exercises_hermes_json_subprocess_contract(tmp_path):
    executable = tmp_path / "fake-hermes"
    executable.write_text(
        "#!/bin/sh\n"
        "cat >/dev/null\n"
        'printf \'%s\\n\' \'{"success":true,"platform":"telegram",'
        '"chat_id":"123456789","message_id":"42"}\'\n'
    )
    executable.chmod(0o755)

    receipt = send_message(
        "test message",
        "telegram:123456789",
        executable=str(executable),
    )

    assert receipt["message_id"] == "42"
