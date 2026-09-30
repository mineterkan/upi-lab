"""Compile contracts/PaymentLedger.sol into contracts/PaymentLedger.json.

Run once, and again whenever the .sol file changes: python compile_contract.py
"""

import json
from pathlib import Path

import solcx

SOLC_VERSION = "0.8.26"
SOURCE = Path("contracts/PaymentLedger.sol")
OUTPUT = Path("contracts/PaymentLedger.json")

solcx.install_solc(SOLC_VERSION)  # downloads the compiler the first time only
compiled = solcx.compile_source(
    SOURCE.read_text(),
    output_values=["abi", "bin"],
    solc_version=SOLC_VERSION,
)
_, contract = compiled.popitem()
OUTPUT.write_text(
    json.dumps({"abi": contract["abi"], "bytecode": contract["bin"]}, indent=2)
)
print(f"wrote {OUTPUT}")
