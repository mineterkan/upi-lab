"""A minimal account ledger in PostgreSQL."""

import os

import psycopg

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/upilab")

SCHEMA = """
DROP TABLE IF EXISTS transfers, accounts;
CREATE TABLE accounts (
    id            text PRIMARY KEY,
    balance_minor bigint NOT NULL CHECK (balance_minor >= 0)
);
CREATE TABLE transfers (
    id           bigserial PRIMARY KEY,
    src          text NOT NULL REFERENCES accounts(id),
    dst          text NOT NULL REFERENCES accounts(id),
    amount_minor bigint NOT NULL CHECK (amount_minor > 0),
    created_at   timestamptz NOT NULL DEFAULT now()
);
"""


class InsufficientFunds(Exception):
    pass


class UnknownAccount(Exception):
    pass


def connect() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL)


def reset() -> None:
    """Delete everything and create empty tables."""
    with connect() as conn:
        conn.execute(SCHEMA)


def open_account(account: str, balance_minor: int) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO accounts (id, balance_minor) VALUES (%s, %s)", (account, balance_minor))


def balance(account: str) -> int:
    with connect() as conn:
        row = conn.execute("SELECT balance_minor FROM accounts WHERE id = %s", (account,)).fetchone()
    if row is None:
        raise UnknownAccount(account)
    return row[0]


def transfer(src: str, dst: str, amount_minor: int) -> int:
    """Move money from src to dst in its own transaction. Returns the transfer id."""
    with connect() as conn:
        with conn.transaction():
            return transfer_in(conn, src, dst, amount_minor)


def transfer_in(conn: psycopg.Connection, src: str, dst: str, amount_minor: int,
                transfer_id: str | None = None) -> int:
    """Move money inside a transaction the caller has already opened.

    The caller decides when to commit, so the transfer can be combined with
    other changes (like marking a payment request as paid) into one atomic unit.
    transfer_id is not needed here: this ledger commits together with the
    switch, so it can never run twice for one request. It is accepted so all
    ledgers can be called the same way.
    """
    if amount_minor <= 0:
        raise ValueError("amount must be positive")
    if src == dst:
        raise ValueError("cannot pay yourself")

    # 1. Lock both account rows, always in the same order.
    locked = conn.execute(
        "SELECT id FROM accounts WHERE id = ANY(%s) ORDER BY id FOR UPDATE",
        ([src, dst],),
    ).fetchall()
    if len(locked) != 2:
        raise UnknownAccount(f"{src} or {dst}")

    # 2. Check and subtract in ONE statement.
    debited = conn.execute(
        "UPDATE accounts SET balance_minor = balance_minor - %s "
        "WHERE id = %s AND balance_minor >= %s RETURNING balance_minor",
        (amount_minor, src, amount_minor),
    ).fetchone()
    if debited is None:
        raise InsufficientFunds(src)

    # 3. Add to the receiver and record the history.
    conn.execute(
        "UPDATE accounts SET balance_minor = balance_minor + %s WHERE id = %s",
        (amount_minor, dst),
    )
    return conn.execute(
        "INSERT INTO transfers (src, dst, amount_minor) "
        "VALUES (%s, %s, %s) RETURNING id",
        (src, dst, amount_minor),
    ).fetchone()[0]
