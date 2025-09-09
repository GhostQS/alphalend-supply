#!/usr/bin/env python3
"""
AlphaFi TBTC query for Sui, similar to AlphaLend script.

This script discovers AlphaFi-related dynamic field objects under a provided
parent container (markets/pools registry) and attempts to locate TBTC entries.
It also fetches TBTC price/supply from Blockberry to provide context.

Usage:
  python alphafi_tbtc.py --parent-id <DYNAMIC_FIELDS_PARENT_OBJECT_ID>

Optional args:
  --rpc <SUI_RPC>                 Sui JSON-RPC endpoint (default: mainnet)
  --no-fallback                   Disable Blockberry fallback (price/supply)
  --list-fields                   Include type hints for inspection

Environment (recommended on cloud hosts):
  BLOCKBERRY_API_KEY              API key for Blockberry
  BLOCKBERRY_TIMEOUT_SECONDS      Default 15
  BLOCKBERRY_RETRIES              Default 3

Notes:
- AlphaFi publishes contract/object IDs at: https://docs.alphafi.xyz/
  Provide the correct container object ID to this script via --parent-id.
- The dynamic field object layout may differ per protocol version; this script
  uses a generic parser to extract coin_type and amount-like fields.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import urllib.error
import urllib.parse
from typing import Any, Dict, List, Optional
import os
import time

SUI_MAINNET_RPC = "https://fullnode.mainnet.sui.io:443"
TBTC_COIN_TYPE = (
    "0x77045f1b9f811a7a8fb9ebd085b5b0c55c5cb0d1520ff55f7037f89b5da9f5f1::TBTC::TBTC"
)


class SuiRPCError(RuntimeError):
    pass


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
            raise SuiRPCError(f"RPC error: {out['error']}")
        return out.get("result")

    def suix_getDynamicFields(self, parent_id: str, cursor: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
        return self._call("suix_getDynamicFields", [parent_id, cursor, limit])

    def suix_getDynamicFieldObject(self, parent_id: str, name: Dict[str, Any]) -> Dict[str, Any]:
        return self._call("suix_getDynamicFieldObject", [parent_id, name])


def humanize_amount(amount: int, decimals: int) -> str:
    if decimals <= 0:
        return str(amount)
    scale = 10 ** decimals
    integer = amount // scale
    frac = amount % scale
    frac_str = str(frac).rjust(decimals, "0").rstrip("0")
    return f"{integer}{('.' + frac_str) if frac_str else ''}"


def fetch_blockberry_tbtc() -> Dict[str, Any]:
    """Fetch TBTC coin info from Blockberry coins endpoint (GET)."""
    api_key = os.getenv("BLOCKBERRY_API_KEY", "").strip() or os.getenv("BLOCKBERRY_TOKEN", "").strip()
    if not api_key:
        raise RuntimeError("BLOCKBERRY_API_KEY is not set")
    coin_enc = urllib.parse.quote(TBTC_COIN_TYPE, safe="")
    url = f"https://api.blockberry.one/sui/v1/coins/{coin_enc}"
    headers = {
        "Accept": "application/json",
        "x-api-key": api_key,
        "User-Agent": "alphalend-supply/1.0 (+https://github.com/GhostQS/alphalend-supply)",
        "Connection": "close",
    }
    timeout_s = float(os.getenv("BLOCKBERRY_TIMEOUT_SECONDS", "15"))
    retries = int(os.getenv("BLOCKBERRY_RETRIES", "3"))
    last_err: Optional[str] = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            last_err = f"attempt {attempt+1}/{retries+1}: {e}"
            if attempt < retries:
                time.sleep(min(2 ** attempt, 3))
                continue
            raise RuntimeError(f"Blockberry fetch failed: {last_err}")


def enumerate_dynamic_objects(client: SuiClient, parent_id: str) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    cursor = None
    while True:
        page = client.suix_getDynamicFields(parent_id, cursor=cursor, limit=50)
        data = page.get("data", [])
        for entry in data:
            name = entry.get("name")
            if name is None:
                continue
            try:
                dfo = client.suix_getDynamicFieldObject(parent_id, name)
                obj = (dfo or {}).get("data") or {}
                if obj:
                    results.append({"name": name, "object": obj})
            except SuiRPCError:
                continue
        cursor = page.get("nextCursor")
        if not page.get("hasNextPage"):
            break
    return results


def generic_extract_amounts(content: Dict[str, Any]) -> Dict[str, int]:
    """Heuristically extract amount-like fields from object content."""
    out: Dict[str, int] = {}
    def add_int(key: str, val: Any):
        try:
            out[key] = int(val)
        except Exception:
            pass
    candidates = [
        "balance", "pooled", "pool_balance", "liquidity", "available_liquidity",
        "deposits", "reserves", "total_reserve", "total_liquidity",
    ]
    def walk(d: Any):
        if isinstance(d, dict):
            for k, v in d.items():
                if k in candidates and isinstance(v, (int, str)):
                    add_int(k, v)
                else:
                    walk(v)
        elif isinstance(d, list):
            for i in d:
                walk(i)
    # dive into content.fields if present
    if isinstance(content, dict):
        fields = content.get("fields")
        walk(fields if isinstance(fields, dict) else content)
    return out


def find_coin_type(content: Dict[str, Any]) -> Optional[str]:
    # Try content.type first
    t = content.get("type") or content.get("dataType")
    if isinstance(t, str) and TBTC_COIN_TYPE in t:
        return TBTC_COIN_TYPE
    # Look for nested coin_type fields like { coin_type: { fields: { name: <hex>::TBTC::TBTC } } }
    def walk(d: Any) -> Optional[str]:
        if isinstance(d, dict):
            name = d.get("name")
            if isinstance(name, str) and "::TBTC::TBTC" in name:
                return ("0x" + name) if not name.startswith("0x") else name
            for v in d.values():
                ct = walk(v)
                if ct:
                    return ct
        elif isinstance(d, list):
            for i in d:
                ct = walk(i)
                if ct:
                    return ct
        elif isinstance(d, str) and TBTC_COIN_TYPE in d:
            return TBTC_COIN_TYPE
        return None
    return walk(content)


def query_alphafi_tbtc(parent_id: str, endpoint: str = SUI_MAINNET_RPC, allow_fallback: bool = True, list_fields: bool = False) -> Dict[str, Any]:
    client = SuiClient(endpoint)
    objects = enumerate_dynamic_objects(client, parent_id)

    tbtc_entries: List[Dict[str, Any]] = []
    inspections: List[Dict[str, Any]] = []

    for entry in objects:
        obj = entry.get("object") or {}
        content = obj.get("content") or {}
        ct = find_coin_type(content if isinstance(content, dict) else {})
        if ct == TBTC_COIN_TYPE:
            amounts = generic_extract_amounts(content)
            tbtc_entries.append({
                "dynamic_field_name": entry.get("name"),
                "object_id": obj.get("objectId"),
                "object_type": obj.get("type"),
                "coin_type": ct,
                "amounts": amounts,
            })
        elif list_fields:
            inspections.append({
                "dynamic_field_name": entry.get("name"),
                "object_id": obj.get("objectId"),
                "object_type": obj.get("type"),
            })

    result: Dict[str, Any] = {
        "alphafi": {
            "parent_container_id": parent_id,
            "objects_found": len(objects),
            "tbtc_entries": tbtc_entries,
        }
    }

    # Optional: attach Blockberry TBTC price/supply context
    if allow_fallback:
        try:
            bb = fetch_blockberry_tbtc()
            result["tbtc_global"] = {
                "source": "blockberry",
                "price_usd": bb.get("price"),
                "circulating_supply": bb.get("circulatingSupply"),
                "total_supply": bb.get("supply"),
                "supply_usd": bb.get("supplyInUsd"),
                "market_cap_usd": bb.get("marketCap"),
                "total_volume": bb.get("totalVolume"),
                "holders_count": bb.get("holdersCount"),
                "status": "ok",
            }
        except Exception as e:
            result["tbtc_global"] = {
                "source": "blockberry",
                "status": "unavailable",
                "error": str(e),
            }

    return result


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description="Query AlphaFi TBTC data on Sui")
    p.add_argument(
        "--parent-id",
        required=False,
        default=None,
        help="AlphaFi parent container (dynamic fields) object ID. If omitted, reads ALPHAFI_PARENT_ID env var.",
    )
    p.add_argument("--rpc", default=SUI_MAINNET_RPC, help="Sui JSON-RPC endpoint")
    p.add_argument("--no-fallback", action="store_true", help="Disable Blockberry fallback")
    p.add_argument("--list-fields", action="store_true", help="List entries not matching TBTC for inspection")
    args = p.parse_args(argv)

    parent_id = args.parent_id or os.getenv("ALPHAFI_PARENT_ID")
    if not parent_id:
        print(
            "Error: --parent-id is required (or set ALPHAFI_PARENT_ID env var).\n"
            "Find the correct AlphaFi parent container in https://docs.alphafi.xyz/ and pass it via --parent-id.",
            file=sys.stderr,
        )
        return 2

    try:
        out = query_alphafi_tbtc(parent_id=parent_id, endpoint=args.rpc, allow_fallback=(not args.no_fallback), list_fields=args.list_fields)
    except SuiRPCError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
