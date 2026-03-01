"""Check which address is derived from your private key."""
import os
from dotenv import load_dotenv
from eth_account import Account

load_dotenv()

pk = os.getenv("PK")
if not pk.startswith("0x"):
    pk = "0x" + pk

account = Account.from_key(pk)
funder = os.getenv("FUNDER")

print(f"Address derived from PK: {account.address}")
print(f"FUNDER in .env:          {funder}")
print(f"Match: {account.address.lower() == (funder or '').lower()}")
