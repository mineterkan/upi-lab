import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

import chain
import crypto
import ledger
import switch


class Customer:
    """A customer with a phone: an alias and a device key registered at the switch."""

    def __init__(self, alias: str) -> None:
        self.alias = alias
        self.device = crypto.DeviceKey()
        switch.register_key(alias, self.device.public_key)

    def sign(self, request_id: str, key: str, amount_minor: int | None = None) -> str:
        request = switch.get_request(request_id)
        message = crypto.instruction_message({
            "request_id": request_id,
            "payer": self.alias,
            "payee": request["payee"],
            "amount_minor": request["amount_minor"] if amount_minor is None else amount_minor,
            "idempotency_key": key,
        })
        return self.device.sign(message)

    def pay(self, request_id: str, key: str | None = None) -> dict:
        key = key or str(uuid.uuid4())
        return switch.pay(request_id, self.alias, key, self.sign(request_id, key))


@pytest.fixture
def anna():
    return Customer("anna@lab")


@pytest.fixture
def ben():
    return Customer("ben@lab")


@pytest.fixture(autouse=True)
def fresh_database():
    switch.reset()
    switch.open_account("anna@lab", 100_00)
    switch.open_account("ben@lab", 100_00)
    switch.open_account("cafe@lab", 0)


def new_key() -> str:
    return str(uuid.uuid4())


def test_pay_a_request(anna):
    request_id = switch.create_request("cafe@lab", 45_50, "order-1")
    result = anna.pay(request_id)
    assert result["replayed"] is False
    assert ledger.balance("anna@lab") == 54_50
    assert ledger.balance("cafe@lab") == 45_50
    assert switch.request_status(request_id) == "PAID"


def test_retry_with_same_key_charges_once(anna):
    request_id = switch.create_request("cafe@lab", 10_00)
    key = new_key()
    first = anna.pay(request_id, key)
    second = anna.pay(request_id, key)  # the phone retried
    assert second["replayed"] is True
    assert second["payment_id"] == first["payment_id"]
    assert ledger.balance("anna@lab") == 90_00


def test_request_can_only_be_paid_once(anna, ben):
    request_id = switch.create_request("cafe@lab", 10_00)
    anna.pay(request_id)
    with pytest.raises(switch.RequestNotPayable):
        ben.pay(request_id)
    assert ledger.balance("ben@lab") == 100_00


def test_expired_request_cannot_be_paid(anna):
    request_id = switch.create_request("cafe@lab", 10_00, ttl_seconds=-1)
    assert switch.request_status(request_id) == "EXPIRED"
    with pytest.raises(switch.RequestNotPayable):
        anna.pay(request_id)


def test_failed_payment_leaves_request_payable(anna):
    request_id = switch.create_request("cafe@lab", 150_00)
    with pytest.raises(ledger.InsufficientFunds):
        anna.pay(request_id)
    assert switch.request_status(request_id) == "PENDING"
    assert ledger.balance("anna@lab") == 100_00


def test_key_cannot_be_reused_for_another_request(anna):
    key = new_key()
    anna.pay(switch.create_request("cafe@lab", 1_00), key)
    with pytest.raises(switch.PaymentError):
        anna.pay(switch.create_request("cafe@lab", 1_00), key)


def test_retry_storm_charges_once(anna):
    """The same signed payment sent 20 times at once, like a phone retrying on a bad network."""
    request_id = switch.create_request("cafe@lab", 7_00)
    key = new_key()
    signature = anna.sign(request_id, key)
    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda _: switch.pay(request_id, "anna@lab", key, signature), range(20)))
    assert len({r["payment_id"] for r in results}) == 1
    assert ledger.balance("anna@lab") == 93_00


def test_twenty_attempts_one_request(anna):
    """20 different payment attempts on one request at once. Exactly one wins."""
    request_id = switch.create_request("cafe@lab", 1_00)

    def attempt(_):
        try:
            anna.pay(request_id)
            return "paid"
        except switch.RequestNotPayable:
            return "rejected"

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(attempt, range(20)))
    assert results.count("paid") == 1
    assert ledger.balance("anna@lab") == 99_00


# --- signatures ---------------------------------------------------------------

def test_someone_else_cannot_pay_from_annas_account(anna, ben):
    """Ben signs with his own key but claims to be Anna."""
    request_id = switch.create_request("cafe@lab", 10_00)
    key = new_key()
    with pytest.raises(switch.InvalidSignature):
        switch.pay(request_id, "anna@lab", key, ben.sign(request_id, key))
    assert ledger.balance("anna@lab") == 100_00


def test_customer_without_registered_key_cannot_pay():
    request_id = switch.create_request("cafe@lab", 10_00)
    stranger = crypto.DeviceKey()  # never registered
    key = new_key()
    message = crypto.instruction_message({
        "request_id": request_id, "payer": "anna@lab", "payee": "cafe@lab",
        "amount_minor": 10_00, "idempotency_key": key,
    })
    with pytest.raises(switch.InvalidSignature):
        switch.pay(request_id, "anna@lab", key, stranger.sign(message))


def test_signature_must_cover_the_real_amount(anna):
    """Anna's phone was tricked into signing 1 ore for a 10 SEK request."""
    request_id = switch.create_request("cafe@lab", 10_00)
    key = new_key()
    with pytest.raises(switch.InvalidSignature):
        switch.pay(request_id, "anna@lab", key, anna.sign(request_id, key, amount_minor=1))


def test_signature_cannot_be_replayed_with_a_new_key(anna):
    """An attacker copies Anna's signature and resends it with a fresh idempotency key."""
    request_id = switch.create_request("cafe@lab", 10_00)
    key = new_key()
    signature = anna.sign(request_id, key)
    with pytest.raises(switch.InvalidSignature):
        switch.pay(request_id, "anna@lab", new_key(), signature)


# --- choosing a ledger -------------------------------------------------------------

def test_pay_on_the_chained_ledger(anna):
    request_id = switch.create_request("cafe@lab", 45_50, ledger_name="chained")
    anna.pay(request_id)
    assert chain.balance("anna@lab") == 54_50     # money moved on the chain
    assert ledger.balance("anna@lab") == 100_00   # the plain ledger is untouched
    assert chain.verify() == []


def test_unknown_ledger_is_rejected():
    with pytest.raises(ValueError):
        switch.create_request("cafe@lab", 1_00, ledger_name="blockchain")
