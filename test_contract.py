import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

import contract
import ledger

pytestmark = pytest.mark.skipif(not contract.w3.is_connected(), reason="Anvil is not running")


@pytest.fixture(autouse=True)
def fresh_contract():
    contract.reset()
    contract.open_account("anna@lab", 100_00)


def test_payment_moves_money():
    contract.transfer("anna@lab", "cafe@lab", 45_50)
    assert contract.balance("anna@lab") == 54_50
    assert contract.balance("cafe@lab") == 45_50
    assert contract.verify() == []


def test_insufficient_funds():
    with pytest.raises(ledger.InsufficientFunds):
        contract.transfer("anna@lab", "cafe@lab", 150_00)
    assert contract.balance("anna@lab") == 100_00


def test_same_transfer_id_is_refused_the_second_time():
    transfer_id = str(uuid.uuid4())
    contract.transfer_in(None, "anna@lab", "cafe@lab", 10_00, transfer_id)
    with pytest.raises(contract.ContractError, match="duplicate transfer"):
        contract.transfer_in(None, "anna@lab", "cafe@lab", 10_00, transfer_id)
    assert contract.balance("anna@lab") == 90_00


def test_no_overdraft_when_payments_arrive_at_once():
    def pay(_):
        try:
            contract.transfer("anna@lab", "cafe@lab", 10_00)
            return "ok"
        except ledger.InsufficientFunds:
            return "rejected"

    with ThreadPoolExecutor(max_workers=30) as pool:
        results = list(pool.map(pay, range(30)))
    assert results.count("ok") == 10
    assert contract.balance("anna@lab") == 0
    assert contract.verify() == []
