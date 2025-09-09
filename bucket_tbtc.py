#!/usr/bin/env python3
"""
Bucket Protocol TBTC query for Sui mainnet.

Goal: report how much TBTC is locked in Bucket Protocol and an estimated TVL in USD.
This script enumerates dynamic field containers (parent objects) you pass via --parent-id,
searches for entries that reference TBTC coin type, and heuristically extracts amount-like
fields (e.g., collateral balance, pooled, liquidity). It then aggregates TBTC locked and
optionally fetches TBTC price from Blockberry to compute TVL.

Usage examples:
  python bucket_tbtc.py --parent-id <PARENT_OBJECT_ID>
  python bucket_tbtc.py --parent-id <ID1> --parent-id <ID2>
  python bucket_tbtc.py --parent-id <ID1,ID2,ID3>

Optional args:
  --rpc <SUI_RPC>                 Sui JSON-RPC endpoint (default: mainnet)
  --no-fallback                   Disable Blockberry price/supply
  --list-fields                   Include entries not matching TBTC for inspection

Environment (recommended on cloud hosts):
  BLOCKBERRY_API_KEY              API key for Blockberry
  BLOCKBERRY_TIMEOUT_SECONDS      Default 15
  BLOCKBERRY_RETRIES              Default 3

Notes:
- You must provide the correct Bucket Protocol dynamic fields parent object IDs. Check
  https://docs.bucketprotocol.io/ for contract/object IDs. On Sui, only parent container
  object IDs can be enumerated via suix_getDynamicFields.
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

# Default Bucket Protocol dynamic-field parent containers (shared objects) on Sui mainnet
# Provided by user/docs. You can override via --parent-id flags.
DEFAULT_BUCKET_PARENT_IDS: list[str] = [
    # BucketProtocol ID (init_shared_version 6365975)
    "0x9e3dab13212b27f5434416939db5dec6a319d15b89a84fd074d03ece6350d3df",
    # BktTreasury ID (init_shared_version 6365975)
    "0x7032c4d7afd30cd0dd04c924d63f1127de6fcc429968306807091d3ad3ff78b1",
]


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


def fetch_blockberry_tbtc(api_key: Optional[str] = None) -> Dict[str, Any]:
    api_key = (api_key or os.getenv("BLOCKBERRY_API_KEY", "").strip() or os.getenv("BLOCKBERRY_TOKEN", "").strip())
    if not api_key:
        raise RuntimeError("BLOCKBERRY_API_KEY is not set (pass --blockberry-api-key or set env)")
    coin_enc = urllib.parse.quote(TBTC_COIN_TYPE, safe="")
    url = f"https://api.blockberry.one/sui/v1/coins/{coin_enc}"
    headers = {
        "Accept": "application/json",
        "x-api-key": api_key,
        "User-Agent": "bucket-tbtc/1.0 (+https://github.com/GhostQS/alphalend-supply)",
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


def try_find_tbtc(content: Dict[str, Any]) -> bool:
    # Quick checks on content strings
    t = content.get("type") or content.get("dataType")
    if isinstance(t, str) and TBTC_COIN_TYPE in t:
        return True
    # Deep scan
    def walk(d: Any) -> bool:
        if isinstance(d, dict):
            for v in d.values():
                if walk(v):
                    return True
        elif isinstance(d, list):
            for i in d:
                if walk(i):
                    return True
        elif isinstance(d, str):
            if TBTC_COIN_TYPE in d or "::TBTC::TBTC" in d:
                return True
        return False
    return walk(content)


def extract_amount_like_fields(content: Dict[str, Any]) -> Dict[str, int]:
    """Heuristically extract amount-like fields from Bucket objects.
    This will capture common names used for collateral, reserves, pooled, etc.
    """
    out: Dict[str, int] = {}
    def add_int(key: str, val: Any):
        try:
            out[key] = int(val)
        except Exception:
            pass
    candidates = [
        "collateral", "collateral_amount", "pooled", "pool_balance", "liquidity",
        "available_liquidity", "deposits", "reserves", "total_reserve", "total_liquidity",
        "balance", "amount",
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
    if isinstance(content, dict):
        fields = content.get("fields")
        walk(fields if isinstance(fields, dict) else content)
    return out


def query_bucket_tbtc(parent_ids: List[str], endpoint: str = SUI_MAINNET_RPC, allow_fallback: bool = True, list_fields: bool = False, blockberry_api_key: Optional[str] = None) -> Dict[str, Any]:
    client = SuiClient(endpoint)
    all_entries: List[Dict[str, Any]] = []
    inspections: List[Dict[str, Any]] = []

    for parent_id in parent_ids:
        objects = enumerate_dynamic_objects(client, parent_id)
        for entry in objects:
            obj = entry.get("object") or {}
            content = obj.get("content") or {}
            if try_find_tbtc(content if isinstance(content, dict) else {}):
                amounts = extract_amount_like_fields(content)
                all_entries.append({
                    "parent_id": parent_id,
                    "dynamic_field_name": entry.get("name"),
                    "object_id": obj.get("objectId"),
                    "object_type": obj.get("type"),
                    "amounts": amounts,
                })
            elif list_fields:
                inspections.append({
                    "parent_id": parent_id,
                    "dynamic_field_name": entry.get("name"),
                    "object_id": obj.get("objectId"),
                    "object_type": obj.get("type"),
                })

    # Aggregate a best-effort locked TBTC from the amounts we found. Prefer keys in order.
    preferred_keys = [
        "collateral", "collateral_amount", "pooled", "pool_balance", "balance", "amount",
        "reserves", "total_reserve",
    ]
    total_locked_raw = 0
    for e in all_entries:
        am = e.get("amounts", {})
        picked = None
        for k in preferred_keys:
            if isinstance(am.get(k), int):
                picked = am.get(k)
                break
        if isinstance(picked, int):
            total_locked_raw += picked
        e["picked_field"] = k if picked is not None else None
        e["picked_value_raw"] = picked

    # TBTC has 8 decimals
    total_locked_human8 = total_locked_raw / 10**8 if isinstance(total_locked_raw, int) else None

    result: Dict[str, Any] = {
        "bucket": {
            "parents_scanned": parent_ids,
            "tbtc_entries": all_entries,
            "locked_total_raw": total_locked_raw,
            "locked_total_human8": (None if total_locked_human8 is None else f"{total_locked_human8:.8f}"),
        }
    }

    # Optional Blockberry context and TVL
    if allow_fallback:
        price = None
        error = None
        try:
            bb = fetch_blockberry_tbtc(api_key=blockberry_api_key)
            if isinstance(bb, dict):
                price = bb.get("price")
                result["tbtc_global"] = {
                    "source": "blockberry",
                    "price_usd": price,
                    "circulating_supply": bb.get("circulatingSupply"),
                    "total_supply": bb.get("supply"),
                    "status": "ok",
                }
        except Exception as e:
            error = str(e)
            result["tbtc_global"] = {"source": "blockberry", "status": "unavailable", "error": error}

        if price is not None and total_locked_human8 is not None:
            try:
                tvl = float(price) * float(total_locked_human8)
                result["bucket"]["tvl_usd_estimate"] = tvl
            except Exception:
                pass

    if list_fields and inspections:
        result["inspections"] = inspections

    return result


def _parse_parent_ids(arg_vals: Optional[List[str]]) -> List[str]:
    out: List[str] = []
    if not arg_vals:
        return out
    for val in arg_vals:
        if "," in val:
            out.extend([s.strip() for s in val.split(",") if s.strip()])
        else:
            v = val.strip()
            if v:
                out.append(v)
    # unique preserve order
    seen = set()
    uniq: List[str] = []
    for v in out:
        if v not in seen:
            seen.add(v)
            uniq.append(v)
    return uniq


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description="Query Bucket Protocol TBTC locked amount on Sui")
    p.add_argument(
        "--parent-id",
        action="append",
        default=None,
        help="Bucket parent dynamic fields object ID. Repeat or use comma to pass multiple. If omitted, uses built-in defaults.",
    )
    p.add_argument("--rpc", default=SUI_MAINNET_RPC, help="Sui JSON-RPC endpoint")
    p.add_argument("--no-fallback", action="store_true", help="Disable Blockberry price/supply")
    p.add_argument("--list-fields", action="store_true", help="List entries not matching TBTC for inspection")
    p.add_argument("--blockberry-api-key", default=None, help="Override Blockberry API key (else use env)")
    args = p.parse_args(argv)

    parent_ids = _parse_parent_ids(args.parent_id)
    if not parent_ids:
        parent_ids = DEFAULT_BUCKET_PARENT_IDS.copy()

    try:
        out = query_bucket_tbtc(
            parent_ids=parent_ids,
            endpoint=args.rpc,
            allow_fallback=(not args.no_fallback),
            list_fields=args.list_fields,
            blockberry_api_key=args.blockberry_api_key,
        )
    except SuiRPCError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
