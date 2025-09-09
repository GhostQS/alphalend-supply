#!/usr/bin/env python3
"""
FastAPI service exposing TBTC data on Sui (AlphaLend, AlphaFi, Bucket).

Endpoints:
- GET /health
- GET /alphalend/tbtc
- GET /alphafi/tbtc
- GET /bucket/tbtc

Common Query params:
- rpc (optional): Sui JSON-RPC endpoint. Default: https://fullnode.mainnet.sui.io:443
- no_fallback (optional): if present and true, disables external fallback (Blockberry) where applicable. Default: false

Notes:
- External price/supply fallback uses Blockberry via the `x-api-key` header. Provide
  BLOCKBERRY_API_KEY in the environment or use a per-endpoint query param where available.
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
from alphafi_tbtc import query_alphafi_tbtc
from bucket_tbtc import query_bucket_tbtc, DEFAULT_BUCKET_PARENT_IDS

app = FastAPI(title="Sui TBTC API (AlphaLend / AlphaFi / Bucket)", version="0.2.0")


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


@app.get("/alphafi/tbtc")
def get_alphafi_tbtc(
    rpc: str = Query(default=SUI_MAINNET_RPC),
    no_fallback: bool = Query(default=False),
    parent_id: Optional[str] = Query(default=None, description="AlphaFi parent dynamic fields object ID. If omitted, reads ALPHAFI_PARENT_ID env var."),
) -> JSONResponse:
    # Resolve parent id
    pid = parent_id or os.getenv("ALPHAFI_PARENT_ID")
    if not pid:
        return JSONResponse({
            "error": "Missing AlphaFi parent_id (set query param or ALPHAFI_PARENT_ID env var)",
        }, status_code=400)

    data = query_alphafi_tbtc(parent_id=pid, endpoint=rpc, allow_fallback=(not no_fallback), list_fields=False)
    return JSONResponse(data)


@app.get("/bucket/tbtc")
def get_bucket_tbtc(
    rpc: str = Query(default=SUI_MAINNET_RPC),
    no_fallback: bool = Query(default=False),
    parent_id: Optional[str] = Query(default=None, description="Bucket parent dynamic fields object ID(s). Comma-separated to pass multiple. If omitted, uses built-in defaults."),
    blockberry_api_key: Optional[str] = Query(default=None, description="Override Blockberry API key (else read env)"),
) -> JSONResponse:
    # Parse parent ids: comma-separated or default list
    parent_ids = []
    if parent_id:
        parent_ids = [p.strip() for p in parent_id.split(',') if p.strip()]
    if not parent_ids:
        parent_ids = DEFAULT_BUCKET_PARENT_IDS

    data = query_bucket_tbtc(
        parent_ids=parent_ids,
        endpoint=rpc,
        allow_fallback=(not no_fallback),
        list_fields=False,
        blockberry_api_key=blockberry_api_key,
    )
    return JSONResponse(data)


if __name__ == "__main__":
    # For local development: uvicorn api:app --reload --port 8000
    import uvicorn

    uvicorn.run("api:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), reload=True)
