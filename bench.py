"""Experiment 2: speed and cost of the same payment on each ledger.

Same setup for every ledger: 20 customers with 10 000 SEK each, one cafe.
Every payment goes through the full switch: signature check, idempotency,
single-use request, ledger write.

  sequential  payments one after another; time per payment (latency)
  parallel    8 customers paying at the same time; payments per second (throughput)
  gas         for the contract ledger: gas used per payment (the chain's cost unit)

Run:
  python bench.py                                  all three ledgers, instant blocks
  RPC_URL=http://127.0.0.1:8546 python bench.py --label "contract, 1 s blocks" --ledgers contract --n 20
"""

import argparse
import csv
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import contract
import crypto
import switch

CUSTOMERS = [f"c{i:02d}@lab" for i in range(20)]
WORKERS = 8


def setup() -> dict[str, crypto.DeviceKey]:
    """Fresh ledgers, same accounts and balances everywhere, one device key per customer."""
    switch.reset()
    switch.open_account("cafe@lab", 0)
    devices = {}
    for alias in CUSTOMERS:
        switch.open_account(alias, 10_000_00)
        devices[alias] = crypto.DeviceKey()
        switch.register_key(alias, devices[alias].public_key)
    return devices


def one_payment(ledger_name: str, alias: str, device: crypto.DeviceKey) -> float:
    """Create a request (merchant side, not timed), then sign and pay it (timed). Returns ms."""
    request_id = switch.create_request("cafe@lab", 12_50, ledger_name=ledger_name)
    key = str(uuid.uuid4())
    message = crypto.instruction_message({
        "request_id": request_id, "payer": alias, "payee": "cafe@lab",
        "amount_minor": 12_50, "idempotency_key": key,
    })
    signature = device.sign(message)
    start = time.perf_counter()
    switch.pay(request_id, alias, key, signature)
    return (time.perf_counter() - start) * 1000


def percentile(values: list[float], p: int) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1)))]


def gas_per_payment() -> float | None:
    """Average gas of every Transferred event on the current contract."""
    logs = contract._contract().events.Transferred.get_logs(from_block=0)
    if not logs:
        return None
    return statistics.mean(contract.w3.eth.get_transaction_receipt(log.transactionHash).gasUsed for log in logs)


def run(ledger_name: str, label: str, n: int) -> tuple[dict, list[float]]:
    devices = setup()
    for i in range(5):  # warm-up: open connections, load code; not measured
        one_payment(ledger_name, CUSTOMERS[i], devices[CUSTOMERS[i]])

    sequential = [one_payment(ledger_name, CUSTOMERS[i % 20], devices[CUSTOMERS[i % 20]]) for i in range(n)]

    jobs = [CUSTOMERS[i % 20] for i in range(n)]
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(lambda alias: one_payment(ledger_name, alias, devices[alias]), jobs))
    seconds = time.perf_counter() - start

    gas = gas_per_payment() if ledger_name == "contract" else None
    summary = {
        "label": label,
        "ledger": ledger_name,
        "payments": n,
        "p50_ms": round(percentile(sequential, 50), 2),
        "p95_ms": round(percentile(sequential, 95), 2),
        "mean_ms": round(statistics.mean(sequential), 2),
        "parallel_per_second": round(n / seconds, 1),
        "gas_per_payment": round(gas) if gas else "",
    }
    return summary, sequential


def append_csv(path: Path, rows: list[dict]) -> None:
    new = not path.exists()
    with path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        if new:
            writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledgers", nargs="+", default=["plain", "chained", "contract"])
    parser.add_argument("--n", type=int, default=300, help="payments per ledger and mode")
    parser.add_argument("--label", default="instant blocks")
    args = parser.parse_args()

    Path("results").mkdir(exist_ok=True)
    print(f"{'ledger':10}{'p50 ms':>9}{'p95 ms':>9}{'mean ms':>9}{'per sec':>10}{'gas':>9}")
    for ledger_name in args.ledgers:
        summary, latencies = run(ledger_name, args.label, args.n)
        print(f"{ledger_name:10}{summary['p50_ms']:>9}{summary['p95_ms']:>9}{summary['mean_ms']:>9}"
              f"{summary['parallel_per_second']:>10}{str(summary['gas_per_payment']):>9}")
        append_csv(Path("results/bench_summary.csv"), [summary])
        append_csv(Path("results/bench_latency.csv"),
                   [{"label": args.label, "ledger": ledger_name, "ms": round(ms, 3)} for ms in latencies])
    print("appended to results/bench_summary.csv and results/bench_latency.csv")


if __name__ == "__main__":
    main()
