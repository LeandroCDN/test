import os
import time

import requests
from eth_abi import encode
from eth_utils import keccak
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    MarketOrderArgs,
    OrderType,
    BalanceAllowanceParams,
    AssetType,
    TradeParams,
    BookParams,
)
from py_clob_client.order_builder.constants import BUY, SELL

from config import (
    POLYMARKET_HOST,
    CHAIN_ID,
    DATA_API,
    RELAYER_URL,
    AUTO_REDEEM_ENABLED,
)

try:
    from py_builder_relayer_client.client import RelayClient
    from py_builder_relayer_client.models import SafeTransaction, OperationType
    from py_builder_signing_sdk.config import BuilderConfig
    from py_builder_signing_sdk.sdk_types import BuilderApiKeyCreds
except Exception:
    RelayClient = None
    SafeTransaction = None
    OperationType = None
    BuilderConfig = None
    BuilderApiKeyCreds = None


CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
ZERO_BYTES32 = b"\x00" * 32
REDEEM_SELECTOR = keccak(text="redeemPositions(address,bytes32,bytes32,uint256[])")[:4]


def _short_order_response(response):
    if not isinstance(response, dict):
        return "ok"
    order_id = response.get("orderID") or response.get("id") or response.get("orderId")
    status = response.get("status") or response.get("state")
    if order_id and status:
        return f"id={str(order_id)[:12]} status={status}"
    if order_id:
        return f"id={str(order_id)[:12]}"
    if status:
        return f"status={status}"
    return "ok"


def init_client(private_key, signature_type=0, funder=None):
    """
    Initialize and authenticate a ClobClient.

    signature_type:
        0 = EOA (MetaMask, hardware wallet)
        1 = Magic / email wallet
        2 = Browser proxy wallet
    """
    kwargs = {
        "host": POLYMARKET_HOST,
        "key": private_key,
        "chain_id": CHAIN_ID,
        "signature_type": signature_type,
    }
    if funder:
        kwargs["funder"] = funder

    client = ClobClient(**kwargs)
    api_creds = client.create_or_derive_api_creds()
    client.set_api_creds(api_creds)

    print("[TRADER] Client authenticated successfully")
    return client


def get_balance(client):
    """Get available USDC (collateral) balance in dollars."""
    try:
        sig_type = client.builder.sig_type if hasattr(client, "builder") else 2
        bal = client.get_balance_allowance(
            params=BalanceAllowanceParams(
                asset_type=AssetType.COLLATERAL,
                signature_type=sig_type,
            )
        )
        raw = float(bal.get("balance", 0))
        if raw > 1_000_000:
            return raw / 1_000_000
        return raw
    except Exception as e:
        print(f"[TRADER] Error getting balance: {e}")
        return 0.0


def get_token_price(client, token_id):
    """Get the best ask price for a token (what you'd pay to buy)."""
    try:
        result = client.get_price(token_id, side="BUY")
        if isinstance(result, dict):
            return float(result.get("price", 0))
        return float(result)
    except Exception as e:
        print(f"[TRADER] Error getting token price: {e}")
        return None


def get_token_bid(client, token_id):
    """Get the best bid price for a token (what you'd get if you sell)."""
    try:
        result = client.get_price(token_id, side="SELL")
        if isinstance(result, dict):
            return float(result.get("price", 0))
        return float(result)
    except Exception as e:
        print(f"[TRADER] Error getting token bid: {e}")
        return None


def get_token_prices_batch(client, token_ids, side="BUY"):
    """Get prices for a list of token ids in a single request."""
    if not token_ids:
        return {}
    try:
        params = [BookParams(token_id=str(tid), side=side) for tid in token_ids]
        result = client.get_prices(params)
    except Exception as e:
        print(f"[TRADER] Error getting batch prices: {e}")
        return {}

    prices = {}
    side_key = str(side).upper()

    if isinstance(result, dict):
        for tid in token_ids:
            raw = result.get(str(tid))
            if isinstance(raw, dict):
                val = raw.get(side_key) or raw.get(side_key.lower()) or raw.get("price")
            else:
                val = raw
            try:
                prices[str(tid)] = float(val)
            except (TypeError, ValueError):
                continue
        return prices

    if isinstance(result, list):
        for item in result:
            if not isinstance(item, dict):
                continue
            tid = str(item.get("token_id") or item.get("tokenId") or item.get("asset_id") or "")
            if not tid:
                continue
            val = item.get(side_key) or item.get(side_key.lower()) or item.get("price")
            try:
                prices[tid] = float(val)
            except (TypeError, ValueError):
                continue
        return prices

    return {}


def place_bet(client, token_id, amount, dry_run=False):
    """Place a market buy order (Fill-or-Kill). amount is in USDC dollars."""
    if dry_run:
        print(f"[DRY RUN] Would buy ${amount:.2f} of token {token_id[:16]}...")
        return {"dry_run": True, "amount": amount, "token_id": token_id}

    try:
        market_order = MarketOrderArgs(
            token_id=token_id,
            amount=amount,
            side=BUY,
            order_type=OrderType.FOK,
        )
        signed_order = client.create_market_order(market_order)
        response = client.post_order(signed_order, OrderType.FOK)
        print(f"[TRADER] Order placed ({_short_order_response(response)})")
        return response
    except Exception as e:
        print(f"[TRADER] Order failed: {e}")
        return None


def sell_shares(client, token_id, shares, dry_run=False):
    """Sell shares via market order (Fill-or-Kill). amount is in shares."""
    if dry_run:
        print(f"[DRY RUN] Would sell {shares:.2f} shares of token {token_id[:16]}...")
        return {"dry_run": True, "shares": shares, "token_id": token_id}

    try:
        market_order = MarketOrderArgs(
            token_id=token_id,
            amount=shares,
            side=SELL,
            order_type=OrderType.FOK,
        )
        signed_order = client.create_market_order(market_order)
        response = client.post_order(signed_order, OrderType.FOK)
        print(f"[TRADER] Sell order placed ({_short_order_response(response)})")
        return response
    except Exception as e:
        print(f"[TRADER] Sell order failed: {e}")
        return None


def get_entry_fill_details(client, order_response, token_id, fallback_price, fallback_amount):
    """
    Resolve entry fill using user trades; fallback to quoted price.

    Returns dict with:
      - entry_price
      - shares
      - source: "trades" | "fallback"
    """
    fallback_shares = (fallback_amount / fallback_price) if fallback_price > 0 else 0.0

    if not isinstance(order_response, dict):
        return {
            "entry_price": fallback_price,
            "shares": fallback_shares,
            "source": "fallback",
        }

    order_id = (
        order_response.get("orderID")
        or order_response.get("id")
        or order_response.get("orderId")
    )
    if not order_id:
        return {
            "entry_price": fallback_price,
            "shares": fallback_shares,
            "source": "fallback",
        }

    try:
        after_ts = int(time.time()) - 600
        trades = client.get_trades(
            TradeParams(
                asset_id=str(token_id),
                after=after_ts,
            )
        )
    except Exception as e:
        print(f"[TRADER] Error fetching trades for fill resolution: {e}")
        trades = []

    matched = []
    for t in trades or []:
        if str(t.get("taker_order_id", "")).lower() != str(order_id).lower():
            continue
        if str(t.get("side", "")).upper() != "BUY":
            continue
        try:
            price = float(t.get("price", 0))
            size = float(t.get("size", 0))
        except Exception:
            continue
        if price > 0 and size > 0:
            matched.append((price, size))

    if not matched:
        return {
            "entry_price": fallback_price,
            "shares": fallback_shares,
            "source": "fallback",
        }

    total_shares = sum(size for _, size in matched)
    total_notional = sum(price * size for price, size in matched)
    entry_price = (total_notional / total_shares) if total_shares > 0 else fallback_price

    return {
        "entry_price": entry_price,
        "shares": total_shares if total_shares > 0 else fallback_shares,
        "source": "trades",
    }


class AutoRedeemer:
    """
    Auto-redeems resolved positions using Polymarket relayer.

    Requires Builder credentials in env:
      - POLY_BUILDER_API_KEY
      - POLY_BUILDER_SECRET
      - POLY_BUILDER_PASSPHRASE
    """

    def __init__(self, private_key, funder=None):
        self.enabled = bool(AUTO_REDEEM_ENABLED)
        self.relay_client = None
        self.wallet_address = funder
        self.reason_disabled = ""

        if not self.enabled:
            self.reason_disabled = "AUTO_REDEEM_ENABLED=False"
            return

        if RelayClient is None or BuilderConfig is None or BuilderApiKeyCreds is None:
            self.enabled = False
            self.reason_disabled = "Missing relayer sdk dependency"
            return

        builder_key = os.getenv("POLY_BUILDER_API_KEY")
        builder_secret = os.getenv("POLY_BUILDER_SECRET")
        builder_passphrase = os.getenv("POLY_BUILDER_PASSPHRASE")

        if not (builder_key and builder_secret and builder_passphrase):
            self.enabled = False
            self.reason_disabled = "Missing builder credentials in .env"
            return

        builder_config = BuilderConfig(
            local_builder_creds=BuilderApiKeyCreds(
                key=builder_key,
                secret=builder_secret,
                passphrase=builder_passphrase,
            )
        )
        self.relay_client = RelayClient(RELAYER_URL, CHAIN_ID, private_key, builder_config)

        if not self.wallet_address:
            try:
                self.wallet_address = self.relay_client.get_expected_safe()
            except Exception:
                self.wallet_address = None

    def redeem_once(self, max_conditions=3):
        if not self.enabled:
            return {
                "attempted": False,
                "claimed": 0,
                "pending": 0,
                "errors": [self.reason_disabled] if self.reason_disabled else [],
            }

        if not self.wallet_address:
            return {
                "attempted": False,
                "claimed": 0,
                "pending": 0,
                "errors": ["Could not resolve wallet address for positions query"],
            }

        conditions = self._get_redeemable_conditions()
        if not conditions:
            return {"attempted": True, "claimed": 0, "pending": 0, "errors": []}

        claimed = 0
        pending = 0
        errors = []

        for condition_id in conditions[:max_conditions]:
            try:
                tx = self._build_redeem_tx(condition_id)
                resp = self.relay_client.execute(
                    [tx], metadata=f"Auto redeem {condition_id[:12]}"
                )
                txn = self.relay_client.poll_until_state(
                    transaction_id=resp.transaction_id,
                    states=["STATE_MINED", "STATE_CONFIRMED"],
                    fail_state="STATE_FAILED",
                    max_polls=8,
                    poll_frequency=2000,
                )
                if txn is None:
                    pending += 1
                else:
                    claimed += 1
            except Exception as e:
                errors.append(f"{condition_id[:12]}...: {e}")

        return {
            "attempted": True,
            "claimed": claimed,
            "pending": pending,
            "errors": errors,
        }

    def _get_redeemable_conditions(self):
        try:
            resp = requests.get(
                f"{DATA_API}/positions",
                params={
                    "user": self.wallet_address,
                    "redeemable": "true",
                    "sizeThreshold": 0,
                    "limit": 500,
                    "offset": 0,
                },
                timeout=10,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            print(f"[TRADER] Error fetching redeemable positions: {e}")
            return []

        if not isinstance(payload, list):
            return []

        condition_ids = set()
        for p in payload:
            condition_id = p.get("conditionId")
            size = p.get("size", 0)
            if not isinstance(condition_id, str):
                continue
            try:
                if float(size) <= 0:
                    continue
            except Exception:
                continue
            condition_ids.add(condition_id)

        return sorted(condition_ids)

    def _build_redeem_tx(self, condition_id):
        condition_bytes = self._condition_hex_to_bytes32(condition_id)
        data = "0x" + (
            REDEEM_SELECTOR
            + encode(
                ["address", "bytes32", "bytes32", "uint256[]"],
                [USDC_ADDRESS, ZERO_BYTES32, condition_bytes, [1, 2]],
            )
        ).hex()
        return SafeTransaction(
            to=CTF_ADDRESS,
            operation=OperationType.Call,
            data=data,
            value="0",
        )

    @staticmethod
    def _condition_hex_to_bytes32(condition_id):
        if not condition_id.startswith("0x") or len(condition_id) != 66:
            raise ValueError(f"Invalid conditionId format: {condition_id}")
        return bytes.fromhex(condition_id[2:])


def init_auto_redeemer(private_key, funder=None):
    return AutoRedeemer(private_key=private_key, funder=funder)


