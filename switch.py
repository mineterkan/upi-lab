"""The payment switch: payment requests and payments.

A merchant creates a payment request. A customer pays it by signing the
payment with their device key. The switch makes sure the signature is valid,
that a request is paid at most once, and that retrying never charges twice.
"""

import uuid

import chain
import contract
import crypto
import ledger

# The ledgers a payment can run on. All three modules offer the same functions
# (transfer_in, balance, open_account, reset), so the switch can use any of them.
LEDGERS = {"plain": ledger, "chained": chain, "contract": contract}

SCHEMA = """
DROP TABLE IF EXISTS payments, payment_requests, device_keys;
CREATE TABLE device_keys (
    alias      text PRIMARY KEY,
    public_key text NOT NULL
);
CREATE TABLE payment_requests (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    payee        text NOT NULL,
    amount_minor bigint NOT NULL CHECK (amount_minor > 0),
    reference    text NOT NULL DEFAULT '',
    ledger       text NOT NULL DEFAULT 'plain' CHECK (ledger IN ('plain', 'chained', 'contract')),
    status       text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'PAID')),
    expires_at   timestamptz NOT NULL
);
CREATE TABLE payments (
    id              bigserial PRIMARY KEY,
    idempotency_key uuid UNIQUE NOT NULL,
    request_id      uuid NOT NULL REFERENCES payment_requests(id),
    payer           text NOT NULL,
    signature       text NOT NULL,
    transfer_id     bigint,
    created_at      timestamptz NOT NULL DEFAULT now()
);
"""


class PaymentError(Exception):
    pass


class RequestNotPayable(PaymentError):
    """Unknown, already paid, or expired."""


class UnknownRequest(RequestNotPayable):
    """No payment request with this id."""


class InvalidSignature(PaymentError):
    """The payer's device did not sign exactly this payment."""


def reset() -> None:
    """Empty ledger and switch tables."""
    with ledger.connect() as conn:
        conn.execute("DROP TABLE IF EXISTS payments, payment_requests, device_keys")
    ledger.reset()
    chain.reset()
    contract.reset()
    with ledger.connect() as conn:
        conn.execute(SCHEMA)


def open_account(alias: str, balance_minor: int) -> None:
    """Open the same account, with the same balance, on every ledger."""
    for book in LEDGERS.values():
        book.open_account(alias, balance_minor)


def register_key(alias: str, public_key: str) -> None:
    """Bind a device's public key to a customer. A real system would verify
    the person's identity first (UPI checks the SIM card and bank card)."""
    with ledger.connect() as conn:
        conn.execute(
            "INSERT INTO device_keys (alias, public_key) VALUES (%s, %s) "
            "ON CONFLICT (alias) DO UPDATE SET public_key = EXCLUDED.public_key",
            (alias, public_key),
        )


def check_request_id(request_id: str) -> None:
    """Reject anything that is not a UUID before it reaches the database."""
    try:
        uuid.UUID(request_id)
    except ValueError:
        raise UnknownRequest(request_id)


def create_request(payee: str, amount_minor: int, reference: str = "",
                   ttl_seconds: int = 300, ledger_name: str = "plain") -> str:
    """The merchant asks to be paid, on the chosen ledger. Returns the request id."""
    if ledger_name not in LEDGERS:
        raise ValueError(f"unknown ledger {ledger_name!r}")
    with ledger.connect() as conn:
        row = conn.execute(
            "INSERT INTO payment_requests (payee, amount_minor, reference, ledger, expires_at) "
            "VALUES (%s, %s, %s, %s, now() + make_interval(secs => %s)) RETURNING id",
            (payee, amount_minor, reference, ledger_name, ttl_seconds),
        ).fetchone()
    return str(row[0])


def get_request(request_id: str) -> dict:
    """What the customer's phone shows before they approve."""
    check_request_id(request_id)
    with ledger.connect() as conn:
        row = conn.execute(
            "SELECT payee, amount_minor, reference, ledger FROM payment_requests WHERE id = %s",
            (request_id,),
        ).fetchone()
    if row is None:
        raise UnknownRequest(request_id)
    return {"id": request_id, "payee": row[0], "amount_minor": row[1],
            "reference": row[2], "ledger": row[3]}


def request_status(request_id: str) -> str:
    check_request_id(request_id)
    with ledger.connect() as conn:
        row = conn.execute(
            "SELECT status, expires_at <= now() FROM payment_requests WHERE id = %s",
            (request_id,),
        ).fetchone()
    if row is None:
        raise UnknownRequest(request_id)
    status, expired = row
    return "EXPIRED" if status == "PENDING" and expired else status


def pay(request_id: str, payer: str, idempotency_key: str, signature: str) -> dict:
    """Pay a request with a signature from the payer's device. Calling again
    with the same idempotency_key returns the first result instead of paying again."""
    check_request_id(request_id)
    with ledger.connect() as conn:
        with conn.transaction():
            # 1. Consent. Rebuild the instruction from the switch's own record of
            #    the request, so the customer must have signed the real payee and
            #    amount, and check it against the payer's registered key.
            request = conn.execute(
                "SELECT payee, amount_minor FROM payment_requests WHERE id = %s", (request_id,)
            ).fetchone()
            if request is None:
                raise UnknownRequest(request_id)
            key_row = conn.execute(
                "SELECT public_key FROM device_keys WHERE alias = %s", (payer,)
            ).fetchone()
            message = crypto.instruction_message({
                "request_id": request_id, "payer": payer, "payee": request[0],
                "amount_minor": request[1], "idempotency_key": idempotency_key,
            })
            if key_row is None or not crypto.verify(key_row[0], message, signature):
                raise InvalidSignature(payer)

            # 2. Record this attempt. If the key already exists, this is a retry.
            inserted = conn.execute(
                "INSERT INTO payments (idempotency_key, request_id, payer, signature) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (idempotency_key) DO NOTHING RETURNING id",
                (idempotency_key, request_id, payer, signature),
            ).fetchone()
            if inserted is None:
                payment_id, first_request, transfer_id = conn.execute(
                    "SELECT id, request_id, transfer_id FROM payments WHERE idempotency_key = %s",
                    (idempotency_key,),
                ).fetchone()
                if str(first_request) != request_id:
                    raise PaymentError("idempotency key already used for another request")
                return {"payment_id": payment_id, "transfer_id": transfer_id, "replayed": True}
            payment_id = inserted[0]

            # 3. Claim the request. Only one transaction can move it from PENDING to PAID.
            claimed = conn.execute(
                "UPDATE payment_requests SET status = 'PAID' "
                "WHERE id = %s AND status = 'PENDING' AND expires_at > now() "
                "RETURNING payee, amount_minor, ledger",
                (request_id,),
            ).fetchone()
            if claimed is None:
                raise RequestNotPayable(request_id)
            payee, amount_minor, ledger_name = claimed

            # 4. Move the money on the request's ledger. The request id goes along as
            #    the transfer id: the contract ledger cannot join this database
            #    transaction, so it uses the id to refuse ever paying a request twice.
            transfer_id = LEDGERS[ledger_name].transfer_in(
                conn, payer, payee, amount_minor, transfer_id=request_id
            )
            conn.execute("UPDATE payments SET transfer_id = %s WHERE id = %s", (transfer_id, payment_id))
    return {"payment_id": payment_id, "transfer_id": transfer_id, "replayed": False}
