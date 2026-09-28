"""Why transfer() is written the way it is.

Runs 50 payments of 10 SEK at the same moment from an account holding 100 SEK,
first with a naive transfer, then with ledger.transfer().
"""

from concurrent.futures import ThreadPoolExecutor

import ledger


def naive_transfer(src: str, dst: str, amount_minor: int) -> None:
    """Looks correct. Read the balance, check it in Python, write the new value."""
    with ledger.connect() as conn:
        current = conn.execute(
            "SELECT balance_minor FROM accounts WHERE id = %s", (src,)
        ).fetchone()[0]
        if current < amount_minor:
            raise ledger.InsufficientFunds(src)
        conn.execute(
            "UPDATE accounts SET balance_minor = %s WHERE id = %s",
            (current - amount_minor, src),
        )
        conn.execute(
            "UPDATE accounts SET balance_minor = balance_minor + %s WHERE id = %s",
            (amount_minor, dst),
        )


def run(transfer_function) -> None:
    ledger.reset()
    ledger.open_account("anna@lab", 100_00)
    ledger.open_account("cafe@lab", 0)

    def pay(_):
        try:
            transfer_function("anna@lab", "cafe@lab", 10_00)
            return "ok"
        except ledger.InsufficientFunds:
            return "rejected"

    with ThreadPoolExecutor(max_workers=50) as pool:
        results = list(pool.map(pay, range(50)))

    anna, cafe = ledger.balance("anna@lab"), ledger.balance("cafe@lab")
    print(f"  succeeded: {results.count('ok')} of 50")
    print(
        f"  anna: {anna / 100:.2f} SEK   cafe: {cafe / 100:.2f} SEK   total: {(anna + cafe) / 100:.2f} SEK"
    )


print("Naive transfer (read, check in Python, write):")
run(naive_transfer)
print("ledger.transfer (row locks + check-and-subtract in one statement):")
run(ledger.transfer)
