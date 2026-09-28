"""A minimal account ledger in PostgreSQL."""

import os

import psycopg

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/upilab"
)

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
        conn.execute(
            "INSERT INTO accounts (id, balance_minor) VALUES (%s, %s)",
            (account, balance_minor),
        )


def balance(account: str) -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT balance_minor FROM accounts WHERE id = %s", (account,)
        ).fetchone()
    if row is None:
        raise UnknownAccount(account)
    return row[0]


def transfer(src: str, dst: str, amount_minor: int) -> int:
    """Move money from src to dst. All or nothing. Returns the transfer id."""
    if amount_minor <= 0:
        raise ValueError("amount must be positive")
    if src == dst:
        raise ValueError("cannot pay yourself")

    with connect() as conn:
        with conn.transaction():
            locked = conn.execute(
                "SELECT id FROM accounts WHERE id = ANY(%s) ORDER BY id FOR UPDATE",
                ([src, dst],),
            ).fetchall()
            if len(locked) != 2:
                raise UnknownAccount(f"{src} or {dst}")

            debited = conn.execute(
                "UPDATE accounts SET balance_minor = balance_minor - %s "
                "WHERE id = %s AND balance_minor >= %s RETURNING balance_minor",
                (amount_minor, src, amount_minor),
            ).fetchone()
            if debited is None:
                raise InsufficientFunds(src)

            conn.execute(
                "UPDATE accounts SET balance_minor = balance_minor + %s WHERE id = %s",
                (amount_minor, dst),
            )
            transfer_id = conn.execute(
                "INSERT INTO transfers (src, dst, amount_minor) "
                "VALUES (%s, %s, %s) RETURNING id",
                (src, dst, amount_minor),
            ).fetchone()[0]
    return transfer_id
