from concurrent.futures import ThreadPoolExecutor

import pytest

import chain
import ledger
import witness


@pytest.fixture(autouse=True)
def fresh_chain():
    chain.reset()
    witness.clear()
    chain.open_account("anna@lab", 100_00)
    chain.open_account("cafe@lab", 0)
    yield
    witness.clear()


def pay_a_few():
    for amount in [10_00, 20_00, 5_00]:
        chain.transfer("anna@lab", "cafe@lab", amount)


def test_payments_move_money_and_the_chain_verifies():
    pay_a_few()
    assert chain.balance("anna@lab") == 65_00
    assert chain.head()[0] == 4  # 1 opening balance + 3 payments
    assert chain.verify() == []


def test_no_overdraft_when_payments_arrive_at_once():
    def pay(_):
        try:
            chain.transfer("anna@lab", "cafe@lab", 10_00)
            return "ok"
        except ledger.InsufficientFunds:
            return "rejected"

    with ThreadPoolExecutor(max_workers=30) as pool:
        results = list(pool.map(pay, range(30)))
    assert results.count("ok") == 10
    assert chain.verify() == []


def test_editing_a_past_entry_is_detected():
    pay_a_few()
    with ledger.connect() as conn:
        conn.execute("UPDATE chain_entries SET amount_minor = 1 WHERE seq = 2")
    problems = chain.verify()
    assert any("was changed" in p for p in problems)


def test_a_full_rewrite_fools_verify_but_not_the_witness():
    """An insider changes a payment AND recomputes every hash and balance."""
    pay_a_few()
    witness.record()

    with ledger.connect() as conn:
        rows = conn.execute(
            "SELECT seq, src, dst, amount_minor, ts_us FROM chain_entries ORDER BY seq"
        ).fetchall()
        conn.execute("DELETE FROM chain_entries")
        prev = chain.GENESIS
        for seq, src, dst, amount, ts_us in rows:
            if seq == 2:
                amount = 1  # hide most of the first payment
            h = chain.entry_hash(prev, seq, src, dst, amount, ts_us)
            conn.execute(
                "INSERT INTO chain_entries VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (seq, src, dst, amount, ts_us, prev, h),
            )
            prev = h
        conn.execute("UPDATE chain_head SET hash = %s", (prev,))
        # Make the balances agree with the rewritten history.
        conn.execute(
            "UPDATE chain_accounts SET balance_minor = %s WHERE id = 'anna@lab'",
            (100_00 - 1 - 20_00 - 5_00,),
        )
        conn.execute(
            "UPDATE chain_accounts SET balance_minor = %s WHERE id = 'cafe@lab'",
            (1 + 20_00 + 5_00,),
        )

    assert chain.verify() == []  # internally perfect again
    assert witness.check() != []  # but the witness still has the old head
