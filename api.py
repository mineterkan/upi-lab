"""HTTP API for the switch. Run: python -m uvicorn api:app --reload"""

import io
from urllib.parse import urlencode

import segno
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import chain
import contract
import ledger
import switch
import witness
from money import format_sek, parse_sek

app = FastAPI(title="UPI Lab")

DEMO_ACCOUNTS = [("anna@lab", 1000_00), ("ben@lab", 1000_00), ("cafe@lab", 0)]


# --- errors: turn Python exceptions into HTTP status codes -------------------

ERRORS = {
    switch.InvalidSignature: 401,         # Unauthorized: not signed by the payer
    ledger.InsufficientFunds: 402,        # Payment Required
    ledger.UnknownAccount: 404,           # Not Found
    switch.UnknownRequest: 404,           # checked before its parent class below
    switch.RequestNotPayable: 409,        # Conflict: paid, expired or unknown
    switch.PaymentError: 409,
    contract.ContractError: 409,          # e.g. the chain already processed this request
    ValueError: 400,                      # Bad Request: e.g. amount "abc"
}


def fail(error: Exception):
    for kind, status in ERRORS.items():
        if isinstance(error, kind):
            raise HTTPException(status_code=status, detail=f"{type(error).__name__}: {error}")
    raise error


# --- request bodies -------------------------------------------------------------

class KeyIn(BaseModel):
    public_key: str


class RequestIn(BaseModel):
    payee: str
    amount: str          # "45.50", parsed with parse_sek, never a float
    reference: str = ""
    ledger: str = "plain"  # "plain", "chained" or "contract"


class PaymentIn(BaseModel):
    request_id: str
    payer: str
    idempotency_key: str
    signature: str


# --- endpoints --------------------------------------------------------------------

@app.post("/api/admin/reset")
def reset():
    """Delete everything and open the demo accounts."""
    switch.reset()
    witness.clear()
    for alias, balance in DEMO_ACCOUNTS:
        switch.open_account(alias, balance)
    return {"accounts": [alias for alias, _ in DEMO_ACCOUNTS]}


@app.post("/api/customers/{alias}/key")
def register_key(alias: str, body: KeyIn):
    switch.register_key(alias, body.public_key)
    return {"alias": alias, "registered": True}


@app.get("/api/accounts/{alias}")
def account(alias: str, ledger: str = "plain"):
    """?ledger=chained shows the balance on the chained ledger."""
    if ledger not in switch.LEDGERS:
        raise HTTPException(404, "unknown ledger")
    try:
        return {"alias": alias, "ledger": ledger, "balance": format_sek(switch.LEDGERS[ledger].balance(alias))}
    except Exception as e:
        fail(e)


@app.post("/api/payment-requests", status_code=201)
def create_request(body: RequestIn):
    try:
        request_id = switch.create_request(
            body.payee, parse_sek(body.amount), body.reference, ledger_name=body.ledger
        )
    except Exception as e:
        fail(e)
    return get_request(request_id)


@app.get("/api/payment-requests/{request_id}")
def get_request(request_id: str):
    try:
        req = switch.get_request(request_id)
        status = switch.request_status(request_id)
    except Exception as e:
        fail(e)
    uri = "upilab://pay?" + urlencode(
        {"pa": req["payee"], "am": format_sek(req["amount_minor"]), "cu": "SEK", "tr": request_id}
    )
    return req | {"amount": format_sek(req["amount_minor"]), "status": status, "uri": uri}


@app.get("/api/payment-requests/{request_id}/qr.svg")
def request_qr(request_id: str):
    buffer = io.BytesIO()
    segno.make(get_request(request_id)["uri"]).save(buffer, kind="svg", scale=6, border=2)
    return Response(buffer.getvalue(), media_type="image/svg+xml")


@app.post("/api/payments")
def pay(body: PaymentIn):
    try:
        return switch.pay(body.request_id, body.payer, body.idempotency_key, body.signature)
    except Exception as e:
        fail(e)


@app.post("/api/chain/checkpoint")
def checkpoint():
    """Give the chain's current head to the witness."""
    seq, h = witness.record()
    return {"seq": seq, "hash": h}


@app.get("/api/chain/audit")
def audit():
    """Is the chain internally consistent, and does it match the witness?"""
    return {"entries": chain.head()[0], "internal": chain.verify(), "witness": witness.check()}


# --- web pages ----------------------------------------------------------------------
# Mounted last, so every /api/... route above is matched first.
app.mount("/", StaticFiles(directory="web", html=True), name="web")
