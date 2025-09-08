#!/usr/bin/env python3
"""
Simple CLI app to fetch TBTC data from Sui mainnet via JSON-RPC.

Features:
- Get coin metadata (symbol, name, decimals, description)
- Get total supply (raw and human-readable)
- Get balance for a specific owner address (optional)

Usage examples:
  python app.py                 # Fetch metadata and total supply for TBTC
  python app.py --owner <addr>  # Also fetch <addr>'s TBTC balance

Coin type (bTBTC on Sui):
  0x77045f1b9f811a7a8fb9ebd085b5b0c55c5cb0d1520ff55f7037f89b5da9f5f1::TBTC::TBTC

Docs:
- Sui JSON-RPC: https://docs.sui.io/sui-api-ref
- Getting started: https://docs.sui.io/guides/developer/getting-started
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import urllib.error
import urllib.parse
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional


SUI_MAINNET_RPC = "https://fullnode.mainnet.sui.io:443"
TBTC_COIN_TYPE = (
    "0x77045f1b9f811a7a8fb9ebd085b5b0c55c5cb0d1520ff55f7037f89b5da9f5f1::TBTC::TBTC"
)


class SuiRPCError(RuntimeError):
    pass


@dataclass
class CoinMetadata:
    decimals: int
    name: str
    symbol: str
    description: str
    icon_url: Optional[str]


class SuiClient:
    def __init__(self, endpoint: str = SUI_MAINNET_RPC):
        self.endpoint = endpoint
        self._request_id = 0

    def _call(self, method: str, params: list[Any]) -> Any:
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
                out = json.loads(body)
        except urllib.error.HTTPError as e:
            raise SuiRPCError(f"HTTP error: {e.code} {e.reason}") from e
        except urllib.error.URLError as e:
            raise SuiRPCError(f"Network error: {e.reason}") from e
        except json.JSONDecodeError as e:
            raise SuiRPCError("Invalid JSON response from node") from e

        if "error" in out:
            err = out["error"]
            raise SuiRPCError(f"RPC error: {err}")
        return out.get("result")

    def get_coin_metadata(self, coin_type: str) -> CoinMetadata:
        result = self._call("suix_getCoinMetadata", [coin_type])
        if result is None:
            raise SuiRPCError("Coin metadata not found")
        return CoinMetadata(
            decimals=int(result.get("decimals", 0)),
            name=result.get("name", ""),
            symbol=result.get("symbol", ""),
            description=result.get("description", ""),
            icon_url=result.get("iconUrl"),
        )

    def get_total_supply(self, coin_type: str) -> int:
        result = self._call("suix_getTotalSupply", [coin_type])
        # result example: {"value": "123456789"}
        if not isinstance(result, dict) or "value" not in result:
            raise SuiRPCError("Unexpected total supply response format")
        return int(result["value"])

    def get_balance(self, owner: str, coin_type: str) -> int:
        # suix_getBalance(owner, coinType) -> { totalBalance: "...", coinObjectCount: n }
        result = self._call("suix_getBalance", [owner, coin_type])
        if not isinstance(result, dict) or "totalBalance" not in result:
            raise SuiRPCError("Unexpected balance response format")
        return int(result["totalBalance"])


def humanize_amount(amount: int, decimals: int) -> str:
    if decimals <= 0:
        return str(amount)
    scale = 10 ** decimals
    integer = amount // scale
    frac = amount % scale
    frac_str = str(frac).rjust(decimals, "0").rstrip("0")
    return f"{integer}{('.' + frac_str) if frac_str else ''}"


def fetch_blockberry_tbtc() -> Dict[str, Any]:
    """Fetch TBTC info from Blockberry.

    Endpoint: GET https://api.blockberry.one/sui/v1/coins/{coinType}
    Header: x-api-key: $BLOCKBERRY_API_KEY
    """
    api_key = os.getenv("BLOCKBERRY_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("BLOCKBERRY_API_KEY is not set")
    coin_enc = urllib.parse.quote(TBTC_COIN_TYPE, safe="")
    url = f"https://api.blockberry.one/sui/v1/coins/{coin_enc}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "x-api-key": api_key,
            "User-Agent": "alphalend-supply/1.0 (+https://github.com/GhostQS/alphalend-supply)",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_tbtc_data(owner: Optional[str] = None, endpoint: str = SUI_MAINNET_RPC, allow_fallback: bool = True) -> Dict[str, Any]:
    client = SuiClient(endpoint)

    meta = client.get_coin_metadata(TBTC_COIN_TYPE)
    # Some bridged coins might not expose a standard TreasuryCap, making total supply unavailable.
    total_supply: Optional[int] = None
    total_supply_error: Optional[str] = None
    try:
        total_supply = client.get_total_supply(TBTC_COIN_TYPE)
    except SuiRPCError as e:
        total_supply_error = str(e)

    result: Dict[str, Any] = {
        "coin_type": TBTC_COIN_TYPE,
        "metadata": {
            "name": meta.name,
            "symbol": meta.symbol,
            "decimals": meta.decimals,
            "description": meta.description,
            "icon_url": meta.icon_url,
        },
        "total_supply": (
            {
                "raw": str(total_supply),
                "human": humanize_amount(total_supply, meta.decimals),
                "status": "ok",
            }
            if total_supply is not None
            else {
                "status": "unavailable",
                "error": total_supply_error,
            }
        ),
    }

    # Optional external fallback: Blockberry for supply/price when Sui supply unavailable
    if total_supply is None and allow_fallback:
        try:
            bb = fetch_blockberry_tbtc()
            result["supply_fallback"] = {
                "source": "blockberry",
                "circulating_supply": bb.get("circulatingSupply"),
                "total_supply": bb.get("supply"),
                "price_usd": bb.get("price"),
                "market_cap_usd": bb.get("marketCap"),
                "status": "ok",
            }
        except Exception as e:  # network or API change
            result["supply_fallback"] = {
                "source": "blockberry",
                "status": "unavailable",
                "error": str(e),
            }

    if owner:
        try:
            bal = client.get_balance(owner, TBTC_COIN_TYPE)
            result["owner_balance"] = {
                "owner": owner,
                "raw": str(bal),
                "human": humanize_amount(bal, meta.decimals),
            }
        except SuiRPCError as e:
            result["owner_balance_error"] = str(e)

    return result


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Fetch Sui TBTC stats")
    parser.add_argument("--owner", help="Sui address to query balance for", default=None)
    parser.add_argument(
        "--rpc",
        help="Sui JSON-RPC endpoint (default: mainnet)",
        default=SUI_MAINNET_RPC,
    )
    parser.add_argument(
        "--no-fallback",
        help="Disable external fallback (Blockberry) when total supply is unavailable on Sui",
        action="store_true",
    )
    args = parser.parse_args(argv)

    try:
        data = fetch_tbtc_data(owner=args.owner, endpoint=args.rpc, allow_fallback=(not args.no_fallback))
    except SuiRPCError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
