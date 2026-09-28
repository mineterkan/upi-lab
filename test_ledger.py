from concurrent.futures import ThreadPoolExecutor

import pytest

import ledger


@pytest.fixture(autouse=True)
def fresh_database():
    """Runs before every test: empty tables, Anna has 100 SEK, the cafe has 0."""
    ledger.reset()
    ledger.open_account("anna@lab", 100_00)
    ledger.open_account("cafe@lab", 0)


def test_payment_moves_money():
    ledger.transfer("anna@lab", "cafe@lab", 45_50)
    assert ledger.balance("anna@lab") == 54_50
    assert ledger.balance("cafe@lab") == 45_50


def test_insufficient_funds_changes_nothing():
    with pytest.raises(ledger.InsufficientFunds):
        ledger.transfer("anna@lab", "cafe@lab", 150_00)
    assert ledger.balance("anna@lab") == 100_00
    assert ledger.balance("cafe@lab") == 0


def test_unknown_account():
    with pytest.raises(ledger.UnknownAccount):
        ledger.transfer("anna@lab", "nobody@lab", 1_00)


def test_money_is_never_created_or_lost():
    for amount in [10_00, 20_00, 30_00]:
        ledger.transfer("anna@lab", "cafe@lab", amount)
    assert ledger.balance("anna@lab") + ledger.balance("cafe@lab") == 100_00


def test_no_overdraft_when_payments_arrive_at_once():
    """50 payments of 10 SEK at the same moment. Anna has 100 SEK, so exactly 10 may succeed."""

    def pay(_):
        try:
            ledger.transfer("anna@lab", "cafe@lab", 10_00)
            return "ok"
        except ledger.InsufficientFunds:
            return "rejected"

    with ThreadPoolExecutor(max_workers=50) as pool:
        results = list(pool.map(pay, range(50)))

    assert results.count("ok") == 10
    assert ledger.balance("anna@lab") == 0
    assert ledger.balance("cafe@lab") == 100_00
