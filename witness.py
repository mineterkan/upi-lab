"""An outside party that keeps copies of the chain head.

The operator can rewrite its own database, but not copies other people hold.
Here the witness is a file; in reality it could be an auditor, the banks in the
scheme, or a public log.
"""

import json
from pathlib import Path

import chain

WITNESS_FILE = Path(__file__).parent / "witness.jsonl"


def record() -> tuple[int, str]:
    """Hand the current head to the witness."""
    seq, h = chain.head()
    with WITNESS_FILE.open("a") as f:
        f.write(json.dumps({"seq": seq, "hash": h}) + "\n")
    return seq, h


def check() -> list[str]:
    """Does the chain still agree with everything the witness was given?"""
    if not WITNESS_FILE.exists():
        return []
    problems = []
    for line in WITNESS_FILE.read_text().splitlines():
        cp = json.loads(line)
        if chain.hash_at(cp["seq"]) != cp["hash"]:
            problems.append(f"entry {cp['seq']} no longer matches the witness's copy")
    return problems


def clear() -> None:
    WITNESS_FILE.unlink(missing_ok=True)
