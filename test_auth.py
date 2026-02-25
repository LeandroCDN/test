"""
Debug script to test different authentication configurations.
Run: python test_auth.py
"""
import os
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

load_dotenv()

PK = os.getenv("PK")
FUNDER = os.getenv("FUNDER")
HOST = "https://clob.polymarket.com"
CHAIN_ID = 137

print("=" * 60)
print("Auth Debug Tool")
print(f"  PK: {PK[:8]}...{PK[-4:]}")
print(f"  FUNDER: {FUNDER}")
print("=" * 60)

configs = [
    {"label": "sig_type=0, no funder (pure EOA)", "sig": 0, "funder": None},
    {"label": "sig_type=1, no funder", "sig": 1, "funder": None},
    {"label": "sig_type=1, with funder", "sig": 1, "funder": FUNDER},
    {"label": "sig_type=2, with funder", "sig": 2, "funder": FUNDER},
    {"label": "sig_type=0, with funder", "sig": 0, "funder": FUNDER},
]

for cfg in configs:
    print(f"\n--- Testing: {cfg['label']} ---")
    try:
        kwargs = {"host": HOST, "key": PK, "chain_id": CHAIN_ID, "signature_type": cfg["sig"]}
        if cfg["funder"]:
            kwargs["funder"] = cfg["funder"]

        client = ClobClient(**kwargs)
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
        print(f"  L1/L2 auth: OK (api_key={creds.api_key[:12]}...)")

        bal = client.get_balance_allowance(
            params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        balance = float(bal.get("balance", 0))
        print(f"  Balance: ${balance:.2f} USDC")
        print(f"  >>> THIS CONFIG WORKS <<<")
    except Exception as e:
        print(f"  Failed: {e}")
