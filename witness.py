"""An outside party that keeps copies of a ledger's latest fingerprint.

The operator can rewrite its own database, but not copies other people hold.
Here the witness is a file; in reality it could be an auditor, the banks in the
scheme, or a public log.

Works with any ledger module that has head() and hash_at():
  chain     head = (last entry number, its hash)
  contract  head = (last block number, its block hash)
"""

import json
from pathlib import Path

import chain

WITNESS_FILE = Path(__file__).parent / "witness.jsonl"


def record(book=chain) -> tuple[int, str]:
    """Hand the ledger's current head to the witness."""
    position, h = book.head()
    with WITNESS_FILE.open("a") as f:
        f.write(json.dumps({"ledger": book.__name__, "position": position, "hash": h}) + "\n")
    return position, h


def check(book=chain) -> list[str]:
    """Does the ledger still agree with everything the witness was given?"""
    if not WITNESS_FILE.exists():
        return []
    problems = []
    for line in WITNESS_FILE.read_text().splitlines():
        cp = json.loads(line)
        if cp["ledger"] == book.__name__ and book.hash_at(cp["position"]) != cp["hash"]:
            problems.append(f"{book.__name__} at {cp['position']} no longer matches the witness's copy")
    return problems


def clear() -> None:
    WITNESS_FILE.unlink(missing_ok=True)
