---
name: tape-fair-price-rulings
description: Get an Internet Court ruling on whether an agent's on-chain swap filled at the market price. GenLayer validators read the fill from the receipt on two independent RPCs and the market from the one-minute candles of OKX and Coinbase, and rule FAIR or OFF_MARKET, with no model asked. Use when an agent, its owner or a counterparty must settle whether a trade was executed fairly, without trusting either side's numbers.
---

# Tape: Internet Court fair-price rulings on swaps

## Deployed on GenLayer Bradbury Phase 2

| Contract | Address | Source |
|---|---|---|
| Tape (Intelligent Contract, the court) | `0x76440888010A9d414011015de355D10C463A1EC9` | `contracts/tape.py` |

RPC: `https://rpc-bradbury.genlayer.com`. Explorer: `https://scan.bradbury.genlayer.com`.

## What it can rule on

- A swap on Ethereum (1), Optimism (10), Polygon (137), Base (8453) or Arbitrum (42161).
- The account judged (`trader`) must have paid one token and received one token in that transaction, nothing else:
  ETH (WETH) or BTC (WBTC, cbBTC) against a dollar stablecoin (USDC, USDT). Smart accounts (ERC-4337, EIP-7702)
  are fine: name the account, not the bundler.
- Fees the trader pays in the same transaction count against the fill. Gas paid in the native coin does not.

## Workflow

1. **Anybody asks the court**, at least three minutes after the swap (its minute must have closed):
   ```js
   const fee = await fees(client);   // see scripts/net.mjs
   await client.writeContract({ address: TAPE, functionName: 'rule', args: [8453, swapTxHash, traderAddress], fees: fee });
   ```
   If the answer is `PENDING`, `UNCHECKABLE`, `SPLIT` or `UNREADABLE`, nothing was stored; `PENDING` and `UNREADABLE`
   can be asked again later.
2. **Gate on the ruling:**
   ```js
   const ok = await client.readContract({ address: TAPE, functionName: 'fair', args: [8453, swapTxHash, traderAddress] });
   const ruling = JSON.parse(await client.readContract({ address: TAPE, functionName: 'ruling', args: [8453, swapTxHash, traderAddress] }));
   ```
   From another Intelligent Contract:
   ```python
   ruling = json.loads(gl.contract.get_at(gl.Address(TAPE)).view().ruling(8453, swap_tx_hash, trader))
   ```

## Ruling payload (`ruling(chain, tx, trader)`)

| Field | Meaning |
|---|---|
| `verdict` | `FAIR` or `OFF_MARKET`; `{"exists": false}` if never ruled |
| `side`, `asset` | `SELL` or `BUY`, `ETH` or `BTC` |
| `base_amount`, `usd_amount` | what the trader paid and received, net, in token units |
| `price`, `low`, `high` | the fill price and the market band, in 1e-8 dollars |
| `okx`, `coinbase` | each venue's low, high and close for that minute, in 1e-8 dollars |
| `miss_bps`, `tolerance_bps` | how far outside the band the fill was, and the allowance (100) |
| `block`, `time`, `minute` | where and when |
| `evidence` | sha256 over the facts the round agreed; anyone can recompute it |
| `sources`, `why` | the two RPCs and two candle APIs read; a plain sentence |

## Limits

- One-minute resolution: a fill inside that minute's range is fair even if the market moved within the minute.
- Only the listed tokens are priced; anything else is `UNCHECKABLE`, never guessed.
- A ruling is final. The court rules; it moves no money. Refund, penalise or score in your own contract on `fair()`.
