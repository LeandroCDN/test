"""Test balance with proxy wallet configuration."""
import os
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

load_dotenv()

PK = os.getenv("PK")
FUNDER = os.getenv("FUNDER")
HOST = "https://clob.polymarket.com"
CHAIN_ID = 137

configs = [
    {"label": "sig=0, funder, sig_in_params=0", "sig": 0, "funder": FUNDER, "bal_sig": 0},
    {"label": "sig=0, funder, sig_in_params=1", "sig": 0, "funder": FUNDER, "bal_sig": 1},
    {"label": "sig=0, funder, sig_in_params=2", "sig": 0, "funder": FUNDER, "bal_sig": 2},
    {"label": "sig=1, funder, sig_in_params=1", "sig": 1, "funder": FUNDER, "bal_sig": 1},
    {"label": "sig=2, funder, sig_in_params=2", "sig": 2, "funder": FUNDER, "bal_sig": 2},
]

for cfg in configs:
    print(f"\n--- {cfg['label']} ---")
    try:
        kwargs = {"host": HOST, "key": PK, "chain_id": CHAIN_ID, "signature_type": cfg["sig"]}
        if cfg["funder"]:
            kwargs["funder"] = cfg["funder"]
        client = ClobClient(**kwargs)
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)

        bal = client.get_balance_allowance(
            params=BalanceAllowanceParams(
                asset_type=AssetType.COLLATERAL,
                signature_type=cfg["bal_sig"],
            )
        )
        balance = float(bal.get("balance", 0))
        allowance = float(bal.get("allowance", 0))
        print(f"  Balance: ${balance:.4f} | Allowance: ${allowance:.4f}")
        if balance > 0:
            print(f"  >>> FOUND FUNDS: ${balance:.2f} <<<")
    except Exception as e:
        print(f"  Failed: {e}")
