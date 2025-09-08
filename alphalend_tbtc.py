#!/usr/bin/env python3
"""
AlphaLend TBTC reserve query for Sui.

This script discovers AlphaLend markets via dynamic fields and attempts to locate the TBTC
reserve, reading its pooled/reserve amount on-chain.

If total TBTC supply on Sui (suix_getTotalSupply) is unavailable, this script can optionally
use CoinGecko to compute a percentage-of-supply figure.

Usage:
  python alphalend_tbtc.py                # Query TBTC reserve on AlphaLend
  python alphalend_tbtc.py --no-fallback  # Disable external fallback (CoinGecko)

References:
- AlphaLend constants: https://docs.alphafi.xyz/alphalend/developers/contract-and-object-ids
- SDK constants (prod): https://github.com/AlphaFiTech/alphalend-sdk-js/blob/main/src/constants/prodConstants.ts
- Sui JSON-RPC: https://docs.sui.io/sui-api-ref
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import urllib.error
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple
import os

SUI_MAINNET_RPC = "https://fullnode.mainnet.sui.io:443"
TBTC_COIN_TYPE = (
    "0x77045f1b9f811a7a8fb9ebd085b5b0c55c5cb0d1520ff55f7037f89b5da9f5f1::TBTC::TBTC"
)

# AlphaLend constants (from docs/SDK)
ALPHALEND_LATEST_PACKAGE_ID = (
    "0xc8a5487ce3e5b78644f725f83555e1c65c38f0424a72781ed5de4f0369725c79"
)
LENDING_PROTOCOL_ID = (
    "0x01d9cf05d65fa3a9bb7163095139120e3c4e414dfbab153a49779a7d14010b93"
)
MARKETS_TABLE_ID = (
    "0x2326d387ba8bb7d24aa4cfa31f9a1e58bf9234b097574afb06c5dfb267df4c2e"
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

    def sui_getObject(self, object_id: str) -> Dict[str, Any]:
        options = {
            "showType": True,
            "showContent": True,
            "showOwner": False,
            "showDisplay": False,
            "showBcs": False,
            "showPreviousTransaction": False,
            "showStorageRebate": False,
        }
        result = self._call("sui_getObject", [{"id": object_id, "options": options}])
        if not isinstance(result, dict) or "data" not in result:
            raise SuiRPCError("Unexpected sui_getObject response format")
        return result["data"]

    def suix_getDynamicFields(self, parent_id: str, cursor: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
        # Signature: suix_getDynamicFields(parentId, cursor, limit)
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


def fetch_coingecko_tbtc() -> Dict[str, Any]:
    url = "https://api.coingecko.com/api/v3/coins/tbtc?localization=false&tickers=false&market_data=true&community_data=false&developer_data=false&sparkline=false"
    headers = {
        "Accept": "application/json",
        "User-Agent": "alphalend-supply/1.0 (+https://github.com/GhostQS/alphalend-supply)",
    }
    api_key = os.getenv("COINGECKO_API_KEY", "").strip()
    if api_key:
        # CoinGecko demo keys typically start with 'CG-'
        if api_key.startswith("CG-"):
            headers["x-cg-demo-api-key"] = api_key
        else:
            headers["x-cg-pro-api-key"] = api_key
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_blockberry_tbtc() -> Optional[Dict[str, Any]]:
    """Fetch TBTC coin info from Blockberry (requires API key).

    Uses endpoint pattern documented by Blockberry:
    GET /sui/v1/coins/{coinType}
    Headers: x-api-key: <KEY>
    """
    api_key = os.getenv("BLOCKBERRY_API_KEY", "").strip()
    if not api_key:
        return None
    coin = urllib.parse.quote(TBTC_COIN_TYPE, safe="")
    url = f"https://api.blockberry.one/sui/v1/coins/{coin}"
    headers = {
        "Accept": "application/json",
        "x-api-key": api_key,
        "User-Agent": "alphalend-supply/1.0 (+https://github.com/GhostQS/alphalend-supply)",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_coingecko_markets_tbtc() -> Optional[Dict[str, Any]]:
    """Fallback: CoinGecko markets endpoint for TBTC price/supply.
    Returns first list element or None.
    """
    base = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids=tbtc"
    headers = {
        "Accept": "application/json",
        "User-Agent": "alphalend-supply/1.0 (+https://github.com/GhostQS/alphalend-supply)",
    }
    api_key = os.getenv("COINGECKO_API_KEY", "").strip()
    if api_key:
        if api_key.startswith("CG-"):
            headers["x-cg-demo-api-key"] = api_key
        else:
            headers["x-cg-pro-api-key"] = api_key
    req = urllib.request.Request(base, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        arr = json.loads(resp.read().decode("utf-8"))
        if isinstance(arr, list) and arr:
            return arr[0]
    return None


def fetch_coingecko_simple_price_tbtc() -> Optional[float]:
    """Fallback: CoinGecko simple price endpoint for TBTC price.
    Returns price in USD or None.
    """
    url = "https://api.coingecko.com/api/v3/simple/price?ids=tbtc&vs_currencies=usd"
    headers = {
        "Accept": "application/json",
        "User-Agent": "alphalend-supply/1.0 (+https://github.com/GhostQS/alphalend-supply)",
    }
    api_key = os.getenv("COINGECKO_API_KEY", "").strip()
    if api_key:
        if api_key.startswith("CG-"):
            headers["x-cg-demo-api-key"] = api_key
        else:
            headers["x-cg-pro-api-key"] = api_key
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return float(((data.get("tbtc") or {}).get("usd"))) if isinstance((data.get("tbtc") or {}).get("usd"), (int, float)) else None


def enumerate_markets(client: SuiClient) -> List[Dict[str, Any]]:
    """Return a list of market objects under MARKETS_TABLE_ID.

    Each entry is a dict with keys: name (dynamic field name), object (Sui object response data)
    """
    results: List[Dict[str, Any]] = []
    cursor = None
    while True:
        page = client.suix_getDynamicFields(MARKETS_TABLE_ID, cursor=cursor, limit=50)
        data = page.get("data", [])
        for entry in data:
            name = entry.get("name")
            if name is None:
                continue
            # Resolve through dynamic field object call to get the actual market object
            try:
                dfo = client.suix_getDynamicFieldObject(MARKETS_TABLE_ID, name)
                obj = (dfo or {}).get("data") or {}
                if obj:
                    results.append({"name": name, "object": obj})
            except SuiRPCError:
                continue
        cursor = page.get("nextCursor")
        if not page.get("hasNextPage"):
            break
    return results


def find_tbtc_in_object_content(content: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Search object content for any reference to the TBTC coin type.

    Returns a dict with discovered fields when found, else None.
    """
    # Check content type string first
    ctype = content.get("type") or content.get("dataType")
    if isinstance(ctype, str) and TBTC_COIN_TYPE in ctype:
        return content

    # Recursively search fields
    fields = content.get("fields") if isinstance(content, dict) else None
    if isinstance(fields, dict):
        # If fields contain explicit coin type
        for k, v in fields.items():
            if isinstance(v, str) and TBTC_COIN_TYPE in v:
                return content
        # Recurse
        for v in fields.values():
            if isinstance(v, dict):
                hit = find_tbtc_in_object_content(v)
                if hit is not None:
                    return hit
            elif isinstance(v, list):
                for i in v:
                    if isinstance(i, dict):
                        hit = find_tbtc_in_object_content(i)
                        if hit is not None:
                            return hit
    return None


def extract_reserve_amount_fields(content: Dict[str, Any]) -> Dict[str, Any]:
    """Heuristically extract amount-like fields from a reserve-like object content."""
    out: Dict[str, Any] = {}
    def add_if_int(name: str, val: Any):
        try:
            out[name] = int(val)
        except Exception:
            pass
    # Common names to look for
    candidates = [
        "liquidity", "available_liquidity", "pool_balance", "pooled", "total_reserve",
        "total_liquidity", "cash", "deposits", "reserves", "balance",
    ]
    # Search within fields
    def walk(d: Dict[str, Any]):
        if not isinstance(d, dict):
            return
        for k, v in d.items():
            if isinstance(v, (str, int)) and k in candidates:
                add_if_int(k, v)
            elif isinstance(v, dict):
                walk(v)
            elif isinstance(v, list):
                for i in v:
                    if isinstance(i, dict):
                        walk(i)
    # Start from content fields
    if isinstance(content, dict):
        fields = content.get("fields")
        if isinstance(fields, dict):
            walk(fields)
    return out


def try_extract_coin_types(content: Dict[str, Any]) -> List[str]:
    types: List[str] = []
    def walk(d: Any):
        if isinstance(d, dict):
            # type in type strings
            t = d.get("type") or d.get("dataType")
            if isinstance(t, str):
                types.append(t)
            for v in d.values():
                walk(v)
        elif isinstance(d, list):
            for i in d:
                walk(i)
        elif isinstance(d, str):
            types.append(d)
    walk(content)
    # keep unique and only coin-like substrings perhaps
    uniq = []
    seen = set()
    for s in types:
        if not isinstance(s, str):
            continue
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


def _parse_market_entry(obj: Dict[str, Any]) -> Dict[str, Any]:
    """Extract structured fields from a market dynamic field object.

    Returns keys: coin_type (string 0x...), balance_holding (int), borrowed_amount (int)
    """
    out: Dict[str, Any] = {}
    content = obj.get("content") or {}
    fields = content.get("fields") if isinstance(content, dict) else None
    if not isinstance(fields, dict):
        return out
    value = fields.get("value")
    v_fields = value.get("fields") if isinstance(value, dict) else None
    if not isinstance(v_fields, dict):
        return out
    # coin type is nested name without 0x in DefiLlama adapter
    coin_type_obj = v_fields.get("coin_type")
    ct_fields = coin_type_obj.get("fields") if isinstance(coin_type_obj, dict) else None
    if isinstance(ct_fields, dict):
        name = ct_fields.get("name")
        if isinstance(name, str):
            out["coin_type"] = ("0x" + name) if not name.startswith("0x") else name
    # numeric fields
    def to_int(x: Any) -> Optional[int]:
        try:
            return int(x)
        except Exception:
            return None
    bh = to_int(v_fields.get("balance_holding"))
    if bh is not None:
        out["balance_holding"] = bh
    br = to_int(v_fields.get("borrowed_amount"))
    if br is not None:
        out["borrowed_amount"] = br
    return out


def query_alphalend_tbtc(endpoint: str = SUI_MAINNET_RPC, allow_fallback: bool = True, list_markets: bool = False) -> Dict[str, Any]:
    client = SuiClient(endpoint)

    markets = enumerate_markets(client)
    found: List[Dict[str, Any]] = []

    tbtc_entries: List[Dict[str, Any]] = []
    for entry in markets:
        name = entry.get("name")
        obj = entry.get("object") or {}
        content = obj.get("content") or {}
        # content may be an object with { dataType, type, fields }
        hit = find_tbtc_in_object_content(content if isinstance(content, dict) else {})
        # parse market fields regardless to detect coin type
        parsed = _parse_market_entry(obj)
        if parsed.get("coin_type") == TBTC_COIN_TYPE:
            tbtc_entries.append({
                "market_dynamic_field_name": name,
                "market_object_id": obj.get("objectId"),
                "object_type": obj.get("type"),
                **parsed,
            })
        if hit is not None:
            values = extract_reserve_amount_fields(hit)
            found.append({
                "market_dynamic_field_name": name,
                "market_object_id": obj.get("objectId"),
                "object_type": obj.get("type"),
                "extracted_numeric_fields": values,
            })
        elif list_markets:
            # gather summary for inspection
            types_seen = try_extract_coin_types(content)
            found.append({
                "market_dynamic_field_name": name,
                "market_object_id": obj.get("objectId"),
                "object_type": obj.get("type"),
                "types_seen_sample": types_seen[:10],
            })

    result: Dict[str, Any] = {
        "alphalend": {
            "markets_table_id": MARKETS_TABLE_ID,
            "markets_found": len(markets),
            "tbtc_markets": tbtc_entries,
            "heuristic_tbtc_matches": found,
        }
    }

    # If desired, try to compute percent vs TBTC global supply (CoinGecko)
    if allow_fallback:
        # First, try Blockberry if API key available
        used_source = None
        circ = None
        total = None
        price_usd = None
        try:
            bb = fetch_blockberry_tbtc()
            if isinstance(bb, dict) and bb:
                used_source = "blockberry"
                # Field names based on Blockberry sample/Dune example
                price_usd = bb.get("price")
                total = bb.get("supply")
                circ = bb.get("circulatingSupply")
        except Exception:
            pass

        # If Blockberry unavailable or incomplete, try CoinGecko chain
        if used_source is None or price_usd is None or circ is None or total is None:
            try:
                cg = fetch_coingecko_tbtc()
                md = cg.get("market_data", {})
                price_usd = (md.get("current_price", {}) or {}).get("usd") if price_usd is None else price_usd
                circ = md.get("circulating_supply") if circ is None else circ
                total = md.get("total_supply") if total is None else total

                if price_usd is None or circ is None or total is None:
                    try:
                        mkt = fetch_coingecko_markets_tbtc() or {}
                        price_usd = price_usd if price_usd is not None else mkt.get("current_price")
                        circ = circ if circ is not None else mkt.get("circulating_supply")
                        total = total if total is not None else mkt.get("total_supply")
                    except Exception:
                        pass

                if price_usd is None:
                    try:
                        price_usd = fetch_coingecko_simple_price_tbtc()
                    except Exception:
                        pass

                if used_source is None:
                    used_source = "coingecko"
            except Exception:
                # If CoinGecko also fails, keep used_source as is (may be None)
                pass

        # Assemble tbtc_global (present even if unavailable)
        if used_source is not None and (price_usd is not None or circ is not None or total is not None):
            result["tbtc_global"] = {
                "source": used_source,
                "circulating_supply": circ,
                "total_supply": total,
                "price_usd": price_usd,
                "status": "ok",
            }
        else:
            result["tbtc_global"] = {
                "source": used_source or "external",
                "status": "unavailable",
            }

        # Optional percent vs global using balance_holding if TBTC market found
        # Prefer 'total' then 'circ' from computed values above.
        if tbtc_entries:
            ts = total if isinstance(total, (int, float)) and total else (circ if isinstance(circ, (int, float)) and circ else None)
            if isinstance(ts, (int, float)) and ts:
                entry0 = tbtc_entries[0]
                bh_raw = entry0.get("balance_holding")
                result["percent_estimates"] = {
                    "note": "Reserve balance assumed raw units; human assumes 8 decimals",
                    "balance_holding_raw": bh_raw,
                    "balance_holding_human8": (str(bh_raw / 10**8) if isinstance(bh_raw, int) else None),
                    "global_total_supply": ts,
                }
        except Exception as e:
            result["tbtc_global"] = {
                "source": "coingecko",
                "status": "unavailable",
                "error": str(e),
            }

    return result


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description="Query AlphaLend TBTC reserves on Sui")
    p.add_argument("--rpc", default=SUI_MAINNET_RPC, help="Sui JSON-RPC endpoint")
    p.add_argument("--no-fallback", action="store_true", help="Disable CoinGecko fallback")
    p.add_argument("--list-markets", action="store_true", help="List markets and show type hints (for debugging)")
    args = p.parse_args(argv)
    try:
        out = query_alphalend_tbtc(endpoint=args.rpc, allow_fallback=(not args.no_fallback), list_markets=args.list_markets)
    except SuiRPCError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
