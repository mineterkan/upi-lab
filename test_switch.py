import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

import ledger
import switch


@pytest.fixture(autouse=True)
def fresh_database():
    switch.reset()
    ledger.open_account("anna@lab", 100_00)
    ledger.open_account("ben@lab", 100_00)
    ledger.open_account("cafe@lab", 0)


def new_key() -> str:
    return str(uuid.uuid4())


def test_pay_a_request():
    request_id = switch.create_request("cafe@lab", 45_50, "order-1")
    result = switch.pay(request_id, "anna@lab", new_key())
    assert result["replayed"] is False
    assert ledger.balance("anna@lab") == 54_50
    assert ledger.balance("cafe@lab") == 45_50
    assert switch.request_status(request_id) == "PAID"


def test_retry_with_same_key_charges_once():
    request_id = switch.create_request("cafe@lab", 10_00)
    key = new_key()
    first = switch.pay(request_id, "anna@lab", key)
    second = switch.pay(request_id, "anna@lab", key)  # the phone retried
    assert second["replayed"] is True
    assert second["payment_id"] == first["payment_id"]
    assert ledger.balance("anna@lab") == 90_00


def test_request_can_only_be_paid_once():
    request_id = switch.create_request("cafe@lab", 10_00)
    switch.pay(request_id, "anna@lab", new_key())
    with pytest.raises(switch.RequestNotPayable):
        switch.pay(request_id, "ben@lab", new_key())
    assert ledger.balance("ben@lab") == 100_00


def test_expired_request_cannot_be_paid():
    request_id = switch.create_request("cafe@lab", 10_00, ttl_seconds=-1)
    assert switch.request_status(request_id) == "EXPIRED"
    with pytest.raises(switch.RequestNotPayable):
        switch.pay(request_id, "anna@lab", new_key())


def test_failed_payment_leaves_request_payable():
    request_id = switch.create_request("cafe@lab", 150_00)
    with pytest.raises(ledger.InsufficientFunds):
        switch.pay(request_id, "anna@lab", new_key())
    assert switch.request_status(request_id) == "PENDING"
    assert ledger.balance("anna@lab") == 100_00


def test_key_cannot_be_reused_for_another_request():
    key = new_key()
    switch.pay(switch.create_request("cafe@lab", 1_00), "anna@lab", key)
    with pytest.raises(switch.PaymentError):
        switch.pay(switch.create_request("cafe@lab", 1_00), "anna@lab", key)


def test_retry_storm_charges_once():
    """The same payment sent 20 times at once, like a phone retrying on a bad network."""
    request_id = switch.create_request("cafe@lab", 7_00)
    key = new_key()
    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(
            pool.map(lambda _: switch.pay(request_id, "anna@lab", key), range(20))
        )
    assert len({r["payment_id"] for r in results}) == 1
    assert ledger.balance("anna@lab") == 93_00


def test_twenty_people_one_request():
    """20 different payment attempts on one request at once. Exactly one wins."""
    request_id = switch.create_request("cafe@lab", 1_00)

    def attempt(_):
        try:
            switch.pay(request_id, "anna@lab", new_key())
            return "paid"
        except switch.RequestNotPayable:
            return "rejected"

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(attempt, range(20)))
    assert results.count("paid") == 1
    assert ledger.balance("anna@lab") == 99_00
