"""A hash-chained ledger in PostgreSQL.

Every transfer is appended to a journal. Each entry stores the hash of the
entry before it, so changing any past entry breaks every hash after it.
"""

import hashlib
import time

import ledger
from ledger import InsufficientFunds, UnknownAccount

MINT = "__mint__"      # where opening balances come from
GENESIS = "0" * 64     # the "previous hash" of the very first entry

SCHEMA = f"""
DROP TABLE IF EXISTS chain_entries, chain_head, chain_accounts;
CREATE TABLE chain_accounts (
    id            text PRIMARY KEY,
    balance_minor bigint NOT NULL CHECK (balance_minor >= 0)
);
CREATE TABLE chain_entries (
    seq          bigint PRIMARY KEY,
    src          text NOT NULL,
    dst          text NOT NULL,
    amount_minor bigint NOT NULL CHECK (amount_minor > 0),
    ts_us        bigint NOT NULL,
    prev_hash    text NOT NULL,
    hash         text NOT NULL
);
CREATE TABLE chain_head (
    id   int PRIMARY KEY CHECK (id = 1),
    seq  bigint NOT NULL,
    hash text NOT NULL
);
INSERT INTO chain_head VALUES (1, 0, '{GENESIS}');
"""


def entry_hash(prev_hash: str, seq: int, src: str, dst: str, amount_minor: int, ts_us: int) -> str:
    """SHA-256 over the previous hash plus this entry's content."""
    text = f"{prev_hash}|{seq}|{src}|{dst}|{amount_minor}|{ts_us}"
    return hashlib.sha256(text.encode()).hexdigest()


def reset() -> None:
    with ledger.connect() as conn:
        conn.execute(SCHEMA)


def _append(conn, src: str, dst: str, amount_minor: int) -> int:
    """Add one entry at the end of the chain. Caller holds the transaction."""
    # Lock the head: only one writer at a time can extend the chain.
    seq, prev = conn.execute("SELECT seq, hash FROM chain_head WHERE id = 1 FOR UPDATE").fetchone()
    seq += 1
    ts_us = time.time_ns() // 1000
    h = entry_hash(prev, seq, src, dst, amount_minor, ts_us)
    conn.execute(
        "INSERT INTO chain_entries VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (seq, src, dst, amount_minor, ts_us, prev, h),
    )
    conn.execute("UPDATE chain_head SET seq = %s, hash = %s WHERE id = 1", (seq, h))
    return seq


def open_account(account: str, balance_minor: int) -> None:
    with ledger.connect() as conn:
        with conn.transaction():
            conn.execute("INSERT INTO chain_accounts VALUES (%s, %s)", (account, balance_minor))
            if balance_minor > 0:
                _append(conn, MINT, account, balance_minor)


def balance(account: str) -> int:
    with ledger.connect() as conn:
        row = conn.execute("SELECT balance_minor FROM chain_accounts WHERE id = %s", (account,)).fetchone()
    if row is None:
        raise UnknownAccount(account)
    return row[0]


def transfer_in(conn, src: str, dst: str, amount_minor: int,
                transfer_id: str | None = None) -> int:
    """Same rules as ledger.transfer_in, plus a chain entry. Returns its seq.
    transfer_id is accepted for the same reason as in ledger.transfer_in."""
    if amount_minor <= 0:
        raise ValueError("amount must be positive")
    if src == dst:
        raise ValueError("cannot pay yourself")
    locked = conn.execute(
        "SELECT id FROM chain_accounts WHERE id = ANY(%s) ORDER BY id FOR UPDATE", ([src, dst],)
    ).fetchall()
    if len(locked) != 2:
        raise UnknownAccount(f"{src} or {dst}")
    debited = conn.execute(
        "UPDATE chain_accounts SET balance_minor = balance_minor - %s "
        "WHERE id = %s AND balance_minor >= %s RETURNING 1",
        (amount_minor, src, amount_minor),
    ).fetchone()
    if debited is None:
        raise InsufficientFunds(src)
    conn.execute(
        "UPDATE chain_accounts SET balance_minor = balance_minor + %s WHERE id = %s", (amount_minor, dst)
    )
    return _append(conn, src, dst, amount_minor)


def transfer(src: str, dst: str, amount_minor: int) -> int:
    with ledger.connect() as conn:
        with conn.transaction():
            return transfer_in(conn, src, dst, amount_minor)


def head() -> tuple[int, str]:
    """The latest (seq, hash). This one short value stands for the whole history."""
    with ledger.connect() as conn:
        return conn.execute("SELECT seq, hash FROM chain_head WHERE id = 1").fetchone()


def hash_at(seq: int) -> str | None:
    if seq == 0:
        return GENESIS
    with ledger.connect() as conn:
        row = conn.execute("SELECT hash FROM chain_entries WHERE seq = %s", (seq,)).fetchone()
    return row[0] if row else None


def verify() -> list[str]:
    """Recompute the whole chain and replay all balances. Returns problems found."""
    with ledger.connect() as conn:
        entries = conn.execute(
            "SELECT seq, src, dst, amount_minor, ts_us, prev_hash, hash FROM chain_entries ORDER BY seq"
        ).fetchall()
        balances = dict(conn.execute("SELECT id, balance_minor FROM chain_accounts").fetchall())
    problems = []
    prev = GENESIS
    replayed = {account: 0 for account in balances}
    for expected_seq, (seq, src, dst, amount, ts_us, prev_hash, h) in enumerate(entries, start=1):
        if seq != expected_seq:
            problems.append(f"entry {expected_seq} is missing")
            break
        if prev_hash != prev:
            problems.append(f"entry {seq} does not link to the entry before it")
        if entry_hash(prev_hash, seq, src, dst, amount, ts_us) != h:
            problems.append(f"entry {seq} was changed after it was written")
        prev = h
        if src != MINT:
            replayed[src] = replayed.get(src, 0) - amount
        replayed[dst] = replayed.get(dst, 0) + amount
    if head() != (len(entries), prev):
        problems.append("head does not match the last entry")
    for account, value in balances.items():
        if replayed.get(account, 0) != value:
            problems.append(f"{account}: history says {replayed.get(account, 0)}, balance says {value}")
    return problems
