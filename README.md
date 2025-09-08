# Sui TBTC CLI

A small Python CLI to fetch TBTC metadata and stats from Sui mainnet via JSON-RPC.

- Coin type (bTBTC on Sui):
  `0x77045f1b9f811a7a8fb9ebd085b5b0c55c5cb0d1520ff55f7037f89b5da9f5f1::TBTC::TBTC`
- Sui docs: https://docs.sui.io/guides/developer/getting-started
- API reference: https://docs.sui.io/sui-api-ref

## Features

- Fetch coin metadata: name, symbol, decimals, description, icon URL
- Fetch total supply if available (graceful fallback when TreasuryCap missing)
- Optional: Fetch an address's TBTC balance
- When Sui total supply is unavailable, automatically fetch global TBTC supply/price from CoinGecko (can be disabled)

## Requirements

- Python 3.8+
- No external dependencies (uses Python standard library)

## Usage

From the `sui_tbtc/` directory:

```bash
python3 app.py
```

Example output:

```json
{
  "coin_type": "0x77045f1b9f811a7a8fb9ebd085b5b0c55c5cb0d1520ff55f7037f89b5da9f5f1::TBTC::TBTC",
  "metadata": {
    "name": "tBTC v2",
    "symbol": "TBTC",
    "decimals": 8,
    "description": "Canonical L2/sidechain token implementation for tBTC",
    "icon_url": "https://assets.coingecko.com/coins/images/11224/standard/0x18084fba666a33d37592fa2633fd49a74dd93a88.png"
  },
  "total_supply": {
    "status": "unavailable",
    "error": "RPC error: {'code': -32602, 'message': 'Cannot find object with type [0x2::coin::TreasuryCap<...::TBTC::TBTC>] from [...] package created objects.'}"
  }
  ,
  "supply_fallback": {
    "source": "coingecko",
    "circulating_supply": 5916.10845822,
    "total_supply": 5915.607324160001,
    "price_usd": 111020,
    "market_cap_usd": 656872889,
    "status": "ok"
  }
}
```

To also query an owner's TBTC balance:

```bash
python3 app.py --owner <SUI_ADDRESS>
```

To use a different RPC endpoint:

```bash
python3 app.py --rpc https://fullnode.mainnet.sui.io:443
```

To disable the external fallback (CoinGecko) and show only on-chain results:

```bash
python3 app.py --no-fallback
```

## Notes on supply and TVL

- The `suix_getTotalSupply` RPC relies on a standard `TreasuryCap` object. Some bridged tokens (including TBTC on Sui) do not expose a queryable `TreasuryCap`, so on-chain `total_supply` returns an error and is marked as `unavailable`. In this case the app fetches global TBTC supply and price from CoinGecko as a fallback.
- "TVL" typically refers to value locked in DeFi protocols on Sui. Calculating TVL accurately requires aggregating balances from known protocol pool objects (e.g., Cetus/Turbos liquidity pools, lending markets), or using a third-party indexer (e.g., DeFiLlama).

If you want, we can extend this CLI to:

- Fetch TBTC price from a public API (e.g., CoinGecko) and compute market cap when total supply is available.
- Compute Sui-specific TBTC TVL by summing TBTC balances in known protocol pools.
- Add holder counts and circulating supply estimates via an indexer.

## API server (FastAPI)

A small FastAPI service is included to expose AlphaLend TBTC pooled balance as an HTTP endpoint.

### Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Run

```bash
uvicorn api:app --reload --port 8000
```

### Endpoints

- `GET /health`
- `GET /alphalend/tbtc?rpc=https://fullnode.mainnet.sui.io:443&no_fallback=false`

Example:

```bash
curl 'http://127.0.0.1:8000/alphalend/tbtc'
```

Response:

```json
{
  "coin_type": "0x77045f1b9f811a7a8fb9ebd085b5b0c55c5cb0d1520ff55f7037f89b5da9f5f1::TBTC::TBTC",
  "alphalend": {
    "markets_table_id": "0x2326...4c2e",
    "market_object_id": "0x...",
    "balance_holding_raw": 5566768803,
    "balance_holding_human8": "55.66768803",
    "borrowed_amount_raw": 4762220369,
    "borrowed_amount_human8": "47.62220369"
  },
  "fallback": {
    "source": "coingecko",
    "circulating_supply": 5915.607324160001,
    "total_supply": 5915.607324160001,
    "price_usd": 111000
  }
}
```
