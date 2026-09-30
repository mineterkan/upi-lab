"""Experiment 1: which ledger notices when an insider cheats?

For each ledger, Anna makes three signed payments to the cafe (10, 20, 30 SEK).
The witness receives the ledger's head. Then an insider with full access to the
database and the chain attacks the first payment:

  naive_edit    change a stored amount or balance, nothing else
  full_rewrite  change the first payment to 0.01 SEK and fix up everything
                else so the ledger is internally consistent again

Two checks run afterwards: the ledger's own verify(), and the witness.

Run: python tamper.py      (needs Postgres and Anvil running)
"""

import csv
import uuid
from pathlib import Path

from eth_utils import keccak

import chain
import contract
import crypto
import ledger
import switch
import witness

LEDGERS = ["plain", "chained", "contract"]
ATTACKS = ["naive_edit", "full_rewrite"]
PAYMENTS = [10_00, 20_00, 30_00]


def pay(device: crypto.DeviceKey, ledger_name: str, amount_minor: int) -> str:
    """Merchant creates a request, Anna's device signs it, the switch pays it."""
    request_id = switch.create_request("cafe@lab", amount_minor, ledger_name=ledger_name)
    key = str(uuid.uuid4())
    message = crypto.instruction_message({
        "request_id": request_id, "payer": "anna@lab", "payee": "cafe@lab",
        "amount_minor": amount_minor, "idempotency_key": key,
    })
    switch.pay(request_id, "anna@lab", key, device.sign(message))
    return request_id


# --- the attacks ---------------------------------------------------------------------

def attack_plain(attack: str) -> None:
    with ledger.connect() as conn:
        first = conn.execute("SELECT min(id) FROM transfers").fetchone()[0]
        conn.execute("UPDATE transfers SET amount_minor = 1 WHERE id = %s", (first,))
        if attack == "full_rewrite":
            hidden = PAYMENTS[0] - 1
            conn.execute("UPDATE accounts SET balance_minor = balance_minor + %s WHERE id = 'anna@lab'", (hidden,))
            conn.execute("UPDATE accounts SET balance_minor = balance_minor - %s WHERE id = 'cafe@lab'", (hidden,))


def attack_chained(attack: str) -> None:
    with ledger.connect() as conn:
        first = conn.execute("SELECT min(seq) FROM chain_entries WHERE src = 'anna@lab'").fetchone()[0]
        if attack == "naive_edit":
            conn.execute("UPDATE chain_entries SET amount_minor = 1 WHERE seq = %s", (first,))
            return
        # Full rewrite: change the entry, recompute every hash after it, fix balances.
        rows = conn.execute("SELECT seq, src, dst, amount_minor, ts_us FROM chain_entries ORDER BY seq").fetchall()
        conn.execute("DELETE FROM chain_entries")
        prev = chain.GENESIS
        for seq, src, dst, amount, ts_us in rows:
            if seq == first:
                amount = 1
            h = chain.entry_hash(prev, seq, src, dst, amount, ts_us)
            conn.execute("INSERT INTO chain_entries VALUES (%s, %s, %s, %s, %s, %s, %s)",
                         (seq, src, dst, amount, ts_us, prev, h))
            prev = h
        conn.execute("UPDATE chain_head SET hash = %s", (prev,))
        hidden = PAYMENTS[0] - 1
        conn.execute("UPDATE chain_accounts SET balance_minor = balance_minor + %s WHERE id = 'anna@lab'", (hidden,))
        conn.execute("UPDATE chain_accounts SET balance_minor = balance_minor - %s WHERE id = 'cafe@lab'", (hidden,))


def attack_contract(attack: str, snapshot: str) -> None:
    w3 = contract.w3
    if attack == "naive_edit":
        # Write straight into the contract's storage: give Anna 10 SEK back.
        # balanceOf is the contract's first storage variable, so it lives in slot 0,
        # and balanceOf[key] is stored at keccak256(key + slot).
        slot = keccak(contract._account_id("anna@lab") + (0).to_bytes(32, "big"))
        new_balance = contract.balance("anna@lab") + PAYMENTS[0]
        w3.provider.make_request("anvil_setStorageAt", [
            contract.ADDRESS_FILE.read_text().strip(), "0x" + slot.hex(), "0x" + new_balance.to_bytes(32, "big").hex(),
        ])
        return
    # Full rewrite: the operator runs the only node, so it can roll the whole
    # chain back to just after deployment and replay history with one change.
    w3.provider.make_request("evm_revert", [snapshot])
    contract.open_account("anna@lab", 1000_00)
    for i, amount in enumerate(PAYMENTS):
        contract.transfer("anna@lab", "cafe@lab", 1 if i == 0 else amount)


# --- one scenario ------------------------------------------------------------------

def run(ledger_name: str, attack: str) -> dict:
    switch.reset()
    witness.clear()
    snapshot = contract.w3.provider.make_request("evm_snapshot", [])["result"]
    switch.open_account("anna@lab", 1000_00)
    switch.open_account("cafe@lab", 0)
    device = crypto.DeviceKey()
    switch.register_key("anna@lab", device.public_key)
    for amount in PAYMENTS:
        pay(device, ledger_name, amount)

    book = switch.LEDGERS[ledger_name]
    has_head = hasattr(book, "head")
    if has_head:
        witness.record(book)

    if ledger_name == "plain":
        attack_plain(attack)
    elif ledger_name == "chained":
        attack_chained(attack)
    else:
        attack_contract(attack, snapshot)

    internal = book.verify() if hasattr(book, "verify") else None
    witnessed = witness.check(book) if has_head else None
    return {
        "ledger": ledger_name,
        "attack": attack,
        "internal_check": "no check" if internal is None else ("DETECTED" if internal else "missed"),
        "witness": "no witness" if witnessed is None else ("DETECTED" if witnessed else "missed"),
    }


def main() -> None:
    rows = [run(ledger_name, attack) for ledger_name in LEDGERS for attack in ATTACKS]
    print(f"{'ledger':10}{'attack':15}{'internal check':16}{'witness'}")
    for r in rows:
        print(f"{r['ledger']:10}{r['attack']:15}{r['internal_check']:16}{r['witness']}")
    Path("results").mkdir(exist_ok=True)
    with open("results/tamper.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print("wrote results/tamper.csv")


if __name__ == "__main__":
    main()
