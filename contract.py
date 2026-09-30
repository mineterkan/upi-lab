"""A ledger that lives in a smart contract on a local Ethereum chain (Anvil).

Same functions as ledger.py and chain.py, so the switch can use it the same way.
"""

import json
import os
import threading
import uuid
from pathlib import Path

from web3 import Web3
from web3.exceptions import ContractLogicError

from ledger import InsufficientFunds

RPC_URL = os.getenv("RPC_URL", "http://127.0.0.1:8545")
ARTIFACT = Path(__file__).parent / "contracts" / "PaymentLedger.json"
ADDRESS_FILE = Path(__file__).parent / "contract_address.txt"

w3 = Web3(Web3.HTTPProvider(RPC_URL))

# Every transaction from one account carries a counter called a nonce: 0, 1, 2...
# If two threads send at the same moment they can get the same nonce and one
# is rejected. Sending one at a time avoids that; waiting for blocks is still parallel.
_send_lock = threading.Lock()


class ContractError(Exception):
    """The contract refused the transaction for a reason other than funds."""


def _operator() -> str:
    # Anvil starts with 10 funded test accounts that it signs for itself.
    # The first one plays the payment switch.
    return w3.eth.accounts[0]


def _contract():
    artifact = json.loads(ARTIFACT.read_text())
    return w3.eth.contract(address=ADDRESS_FILE.read_text().strip(), abi=artifact["abi"])


def _account_id(alias: str) -> bytes:
    """Solidity has no cheap strings, so an alias is stored as 32 bytes."""
    return alias.encode().ljust(32, b"\0")


def _send(function) -> dict:
    """Send a transaction and wait until it is in a block. Returns the receipt."""
    with _send_lock:
        tx_hash = function.transact({"from": _operator()})
    return w3.eth.wait_for_transaction_receipt(tx_hash, poll_latency=0.01)


def reset() -> None:
    """Deploy a fresh contract. The old one stays on the chain, just unused."""
    artifact = json.loads(ARTIFACT.read_text())
    factory = w3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"])
    receipt = _send(factory.constructor())
    ADDRESS_FILE.write_text(receipt.contractAddress)


def open_account(alias: str, balance_minor: int) -> None:
    if balance_minor > 0:
        _send(_contract().functions.mint(_account_id(alias), balance_minor))


def balance(alias: str) -> int:
    return _contract().functions.balanceOf(_account_id(alias)).call()


def transfer_in(conn, src: str, dst: str, amount_minor: int, transfer_id: str | None = None) -> int:
    """Move money on the chain. Returns the block number it landed in.

    `conn` is the switch's database transaction. The chain cannot take part in
    it, so this runs on its own. transfer_id makes it safe to retry: the
    contract refuses a transfer id it has already processed.
    """
    key = uuid.UUID(transfer_id or str(uuid.uuid4())).bytes.ljust(32, b"\0")
    call = _contract().functions.transfer(key, _account_id(src), _account_id(dst), amount_minor)
    try:
        receipt = _send(call)
    except ContractLogicError as e:  # the node simulated the call first and it would fail
        if "insufficient funds" in str(e):
            raise InsufficientFunds(src)
        raise ContractError(str(e))
    if receipt.status != 1:  # mined, but reverted (another payment got there first)
        raise InsufficientFunds(src)
    return receipt.blockNumber


def transfer(src: str, dst: str, amount_minor: int) -> int:
    return transfer_in(None, src, dst, amount_minor)


def verify() -> list[str]:
    """Replay every Minted and Transferred event and compare with the stored balances."""
    c = _contract()
    replayed: dict[str, int] = {}
    for event in c.events.Minted.get_logs(from_block=0):
        alias = event.args.account.rstrip(b"\0").decode()
        replayed[alias] = replayed.get(alias, 0) + event.args.amount
    for event in c.events.Transferred.get_logs(from_block=0):
        src = event.args["from"].rstrip(b"\0").decode()
        dst = event.args.to.rstrip(b"\0").decode()
        replayed[src] = replayed.get(src, 0) - event.args.amount
        replayed[dst] = replayed.get(dst, 0) + event.args.amount
    problems = []
    for alias, expected in replayed.items():
        actual = balance(alias)
        if actual != expected:
            problems.append(f"{alias}: history says {expected}, balance says {actual}")
    return problems
