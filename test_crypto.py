import uuid

import pytest

import crypto


def instruction(**changes) -> dict:
    base = {
        "request_id": str(uuid.uuid4()),
        "payer": "anna@lab",
        "payee": "cafe@lab",
        "amount_minor": 45_50,
        "idempotency_key": str(uuid.uuid4()),
    }
    return base | changes


def test_valid_signature_is_accepted():
    device = crypto.DeviceKey()
    instr = instruction()
    signature = device.sign(crypto.instruction_message(instr))
    assert crypto.verify(
        device.public_key, crypto.instruction_message(instr), signature
    )


def test_changing_any_field_breaks_the_signature():
    device = crypto.DeviceKey()
    instr = instruction()
    signature = device.sign(crypto.instruction_message(instr))
    for field, value in [
        ("amount_minor", 45_51),
        ("payee", "evil@lab"),
        ("payer", "ben@lab"),
    ]:
        tampered = instr | {field: value}
        assert not crypto.verify(
            device.public_key, crypto.instruction_message(tampered), signature
        )


def test_someone_elses_key_is_rejected():
    anna, mallory = crypto.DeviceKey(), crypto.DeviceKey()
    message = crypto.instruction_message(instruction())
    assert not crypto.verify(anna.public_key, message, mallory.sign(message))


def test_message_format_is_fixed():
    instr = instruction(request_id="r1", idempotency_key="k1")
    assert crypto.instruction_message(instr) == (
        b"UPILAB-PAY-v1\nrequest_id=r1\npayer=anna@lab\npayee=cafe@lab\n"
        b"amount_minor=4550\nidempotency_key=k1"
    )


def test_newline_injection_is_rejected():
    with pytest.raises(ValueError):
        crypto.instruction_message(instruction(payee="cafe@lab\namount_minor=1"))
