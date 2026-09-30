import uuid

import pytest
from fastapi.testclient import TestClient

import crypto
from api import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def fresh_database():
    client.post("/api/admin/reset")


def signed_payment(device, request: dict, payer: str) -> dict:
    key = str(uuid.uuid4())
    message = crypto.instruction_message(
        {
            "request_id": request["id"],
            "payer": payer,
            "payee": request["payee"],
            "amount_minor": request["amount_minor"],
            "idempotency_key": key,
        }
    )
    return {
        "request_id": request["id"],
        "payer": payer,
        "idempotency_key": key,
        "signature": device.sign(message),
    }


def test_full_payment_over_http():
    phone = crypto.DeviceKey()
    assert (
        client.post(
            "/api/customers/anna@lab/key", json={"public_key": phone.public_key}
        ).status_code
        == 200
    )

    created = client.post(
        "/api/payment-requests", json={"payee": "cafe@lab", "amount": "45.50"}
    )
    assert created.status_code == 201
    request = created.json()
    assert request["uri"].startswith("upilab://pay?pa=cafe%40lab&am=45.50")

    paid = client.post("/api/payments", json=signed_payment(phone, request, "anna@lab"))
    assert paid.status_code == 200
    assert (
        client.get(f"/api/payment-requests/{request['id']}").json()["status"] == "PAID"
    )
    assert client.get("/api/accounts/anna@lab").json()["balance"] == "954.50"


def test_bad_signature_is_401():
    client.post(
        "/api/customers/anna@lab/key",
        json={"public_key": crypto.DeviceKey().public_key},
    )
    request = client.post(
        "/api/payment-requests", json={"payee": "cafe@lab", "amount": "10"}
    ).json()
    response = client.post(
        "/api/payments", json=signed_payment(crypto.DeviceKey(), request, "anna@lab")
    )
    assert response.status_code == 401


def test_bad_amount_is_400():
    response = client.post(
        "/api/payment-requests", json={"payee": "cafe@lab", "amount": "12.345"}
    )
    assert response.status_code == 400


def test_qr_code_is_an_svg():
    request = client.post(
        "/api/payment-requests", json={"payee": "cafe@lab", "amount": "10"}
    ).json()
    response = client.get(f"/api/payment-requests/{request['id']}/qr.svg")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/svg+xml"


def test_malformed_request_id_is_404_not_500():
    assert client.get("/api/payment-requests/not-a-uuid").status_code == 404
    assert client.get("/api/payment-requests/not-a-uuid/qr.svg").status_code == 404
    assert client.get(f"/api/payment-requests/{uuid.uuid4()}").status_code == 404
