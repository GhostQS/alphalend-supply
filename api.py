#!/usr/bin/env python3
"""
FastAPI service exposing AlphaLend TBTC pooled balance on Sui.

Endpoints:
- GET /health
- GET /alphalend/tbtc
    Query params:
      - rpc (optional): Sui JSON-RPC endpoint. Default: https://fullnode.mainnet.sui.io:443
      - no_fallback (optional): if present and true, disables CoinGecko fallback. Default: false

Returns JSON like:
{
  "coin_type": "...::TBTC::TBTC",
  "alphalend": {
    "markets_table_id": "0x...",
    "market_object_id": "0x...",
    "balance_holding_raw": 5566768803,
    "balance_holding_human8": "55.66768803",
    "borrowed_amount_raw": 4762220369,
    "borrowed_amount_human8": "47.62220369"
  },
  "fallback": { ... optional CoinGecko info ... }
}
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from alphalend_tbtc import (
    query_alphalend_tbtc,
    TBTC_COIN_TYPE,
    SUI_MAINNET_RPC,
)

app = FastAPI(title="Sui TBTC AlphaLend API", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/alphalend/tbtc")
def get_alphalend_tbtc(
    rpc: str = Query(default=SUI_MAINNET_RPC),
    no_fallback: bool = Query(default=False),
) -> JSONResponse:
    data = query_alphalend_tbtc(endpoint=rpc, allow_fallback=(not no_fallback))

    # Find TBTC market entry, if any
    tbtc_list = (data.get("alphalend", {}) or {}).get("tbtc_markets", [])
    entry = tbtc_list[0] if tbtc_list else None

    response = {
        "coin_type": TBTC_COIN_TYPE,
        "alphalend": None,
    }

    if entry:
        bh_raw = entry.get("balance_holding")
        br_raw = entry.get("borrowed_amount")
        # 8 decimals for TBTC
        bh_human = (f"{bh_raw/10**8:.8f}" if isinstance(bh_raw, int) else None)
        br_human = (f"{br_raw/10**8:.8f}" if isinstance(br_raw, int) else None)
        response["alphalend"] = {
            "markets_table_id": (data.get("alphalend", {}) or {}).get("markets_table_id"),
            "market_object_id": entry.get("market_object_id"),
            "balance_holding_raw": bh_raw,
            "balance_holding_human8": bh_human,
            "borrowed_amount_raw": br_raw,
            "borrowed_amount_human8": br_human,
        }
    else:
        response["alphalend"] = {
            "error": "TBTC market not found in AlphaLend",
            "markets_table_id": (data.get("alphalend", {}) or {}).get("markets_table_id"),
        }

    # Include fallback info if present
    if "tbtc_global" in data:
        response["fallback"] = data["tbtc_global"]
    if "percent_estimates" in data:
        response["percent_estimates"] = data["percent_estimates"]

    return JSONResponse(response)


@app.get("/alphalend/tbtc/pooled")
def get_alphalend_tbtc_pooled(
    rpc: str = Query(default=SUI_MAINNET_RPC),
    no_fallback: bool = Query(default=False),
):
    data = query_alphalend_tbtc(endpoint=rpc, allow_fallback=(not no_fallback))
    tbtc_list = (data.get("alphalend", {}) or {}).get("tbtc_markets", [])
    entry = tbtc_list[0] if tbtc_list else None
    if not entry:
        return JSONResponse({
            "error": "TBTC market not found in AlphaLend",
            "markets_table_id": (data.get("alphalend", {}) or {}).get("markets_table_id"),
        }, status_code=404)
    bh_raw = entry.get("balance_holding")
    bh_human = (f"{bh_raw/10**8:.8f}" if isinstance(bh_raw, int) else None)
    return JSONResponse({
        "coin_type": TBTC_COIN_TYPE,
        "markets_table_id": (data.get("alphalend", {}) or {}).get("markets_table_id"),
        "market_object_id": entry.get("market_object_id"),
        "balance_holding_raw": bh_raw,
        "balance_holding_human8": bh_human,
    })


if __name__ == "__main__":
    # For local development: uvicorn api:app --reload --port 8000
    import uvicorn

    uvicorn.run("api:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), reload=True)
