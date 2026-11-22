# Tape

**Internet Court rulings on whether an agent's swap filled at the market price. The fill comes from the transaction's
receipt, read from two independent RPCs; the market comes from that minute's candles on OKX and Coinbase; the verdict
is FAIR or OFF_MARKET, and no model is asked.**

An Intelligent Contract primitive, built with **OKX** in mind. Internet Court · GenLayer · OKX.

Agents trade on chain for their owners, and for each other. When an owner says "my agent sold at a terrible price"
or a counterparty says "you were filled fairly", nothing on chain can settle it: the price the market showed at that
minute lives on exchanges, not on the chain. Tape turns the exchanges' public record into a ruling other contracts
can gate on: refund a user, slash an agent's bond, score an executor.

- **The court (GenLayer Bradbury):** [`0x76440888010A9d414011015de355D10C463A1EC9`](https://scan.bradbury.genlayer.com/address/0x76440888010A9d414011015de355D10C463A1EC9)
- **Skill:** [`SKILL.md`](SKILL.md)

```text
anybody ──rule(chain, swap tx, trader)──▶ Tape (GenLayer)
            ├─ the fill:   eth_getTransactionReceipt + eth_getBlockByNumber, from two independent RPCs
            │              the trader's net ERC-20 flows: one asset paid, one received, and the block's minute
            └─ the market: OKX  /api/v5/market/history-candles  (1m, that minute)
                           Coinbase  /products/…/candles      (60 s, that minute)
                           band = lowest low .. highest high of the two
         every validator repeats every read and must reach the same verdict and evidence digest
other contracts ──ruling(…) / fair(…)──▶ the stored ruling
```

## How it rules

`rule(chain_id, tx_hash, trader)` is open to anybody. `trader` is the account whose fill is judged: the agent's own
address, which on Base is usually a smart account (ERC-4337 or EIP-7702), not the bundler that sent the transaction.

| Verdict | When |
|---|---|
| `FAIR` | the fill is inside that minute's band, or outside it by no more than 100 bps (pool fees, price impact) |
| `OFF_MARKET` | the trader sold below the band's low, or bought above its high, by more than 100 bps |

The fill is **net**: everything the trader paid and received in the transaction. A fee taken in the same transaction
counts against the fill, because it is part of what the trader actually got.

No ruling is stored, and the call can be made again where that makes sense, when:

| State | When |
|---|---|
| `PENDING` | the chain does not show the transaction yet, or its minute has not closed (180 s) |
| `UNCHECKABLE` | not a swap for that account (it touched other assets, only received, or was never involved), a reverted transaction, a token the court does not price, a chain it does not read |
| `SPLIT` | the two RPCs show different receipts, or OKX and Coinbase closed that minute more than 200 bps apart |
| `UNREADABLE` | a source could not be read |

Priced: ETH (WETH) and BTC (WBTC, cbBTC) against USDC and USDT, on Ethereum, Optimism, Polygon, Base and Arbitrum.
Every token's decimals are checked against the chain in `tests/tape_rules.py --live`.

**No model is asked anything.** Prices are parsed as text into integers of 1e-8 dollars and compared in integer
arithmetic, so every validator reaches exactly the same digits.

## Why it holds

- **Neither side supplies a number.** The amounts come from the receipt, the market from two exchanges.
- **One bad source cannot decide a case.** Two RPCs must show the same receipt, and two exchanges must agree on the
  minute's close within 200 bps, or there is no ruling.
- **Only that minute counts.** The court asks each exchange for the candle that opens at the swap's minute and refuses
  any other; a minute still open is `PENDING`, not judged on a partial candle.
- **Naming someone else's swap proves nothing about you.** An account the transaction never touched, or one that only
  received (a fee taker), gets no ruling; proven on chain.
- **The whole fill, not the pool's.** A fee skimmed in the same transaction is counted; proven on chain.
- **A ruling is final.** Asking again changes nothing. Each ruling stores the sources it read and an evidence digest.

## Proven on Bradbury, with real Base swaps

`scripts/prove.mjs`, with every transaction in `results/proof.json`. pchg, a stranger to every swap, asks the court.
The swaps were found by scanning eight hours of Base WETH/USDC transfers and kept only where the account's whole fill
is two tokens.

| Case | Swap (Base) | Court (Bradbury) | Verdict |
|---|---|---|---|
| a purchase inside the minute's band | [`0x21193672`](https://basescan.org/tx/0x21193672799778dada49e6680374c7f22f90ce4a17356fbabb11b3e4a5b36d90) | [`0x1600cd97`](https://scan.bradbury.genlayer.com/tx/0x1600cd97a8a59dfa15cc52d15d755096e7be1626bc878b9a491def0ee40018fd) | **FAIR**: bought at $1708.89, the market traded $1708.56 to $1712.82 |
| a purchase over the band, within the tolerance | [`0x3c30c2c2`](https://basescan.org/tx/0x3c30c2c2cab9cd4cc15497e318f0c0d62f05fa93f0dc389c4715733b11b8790e) | [`0x22864381`](https://scan.bradbury.genlayer.com/tx/0x228643815efad6d50a2162aa3341d133e2332bb120e00f6a6d8dcf533b5d7cc2) | **FAIR**: bought at $1727.93, 65 bps over $1712.15 to $1716.73 |
| a sale at the pool's price, with a fee taken in the same transaction | [`0xc64dd151`](https://basescan.org/tx/0xc64dd1516887f7944ce49a7383c3c1510f1ae9bcf733b8b812732c15c1a6c157) | [`0x0ea8fb66`](https://scan.bradbury.genlayer.com/tx/0x0ea8fb6632e34d9f6a245a804010675269e5575ecb5facbfa096b587400f3e40) | **OFF_MARKET**: 0.01458 ETH sold for 24.99 USDC, 0.74 USDC of it taken by a fee: net $1663.19, 299 bps under the $1714.56 low |
| the fee taker in that transaction, who only received | same | [`0x2638a9d7`](https://scan.bradbury.genlayer.com/tx/0x2638a9d7be4cb1c8f8b383680b0d1ee8dbd98dfcb7f4bb11463f0bf02577ef0b) | nothing stored |
| an account the swap never touched | same | [`0xd55b0f59`](https://scan.bradbury.genlayer.com/tx/0xd55b0f590c40dd350ee67d629e67188481193eaca071a14fa94a3790e64102fd) | nothing stored |
| a hash Base never saw | | [`0x07215396`](https://scan.bradbury.genlayer.com/tx/0x072153968d6eb42d7807c8a2c7c059c26f8ffdddd55f4800cfe7b83b056d6293) | nothing stored |
| a chain the court does not read | | [`0xf11736d5`](https://scan.bradbury.genlayer.com/tx/0xf11736d54f6bd2a4d3e4e640adfe05e35df0a3f73991f28377571737179829c6) | nothing stored |
| asking again about a ruled fill | | [`0xb46a36fd`](https://scan.bradbury.genlayer.com/tx/0xb46a36fdeeaf634b9a2b4146a43409669e041edaffc2fd59056cbcbc0d9c30e6) | the ruling stays as it was |

Around them: `fair()` is true for a FAIR fill and false for the OFF_MARKET one and for one never ruled; the
off-market ruling records the net amounts, both venues' candles, the band and the miss; it carries an evidence digest;
and the court lists exactly the three rulings. All 14 checks pass.

`node scripts/verify.mjs` needs no keys: it compares the deployed source with `contracts/tape.py` byte for byte
(MATCH) and reads the status of every transaction above (all 9 finalized).

## Tests

`python tests/tape_rules.py`: 36 checks through the real contract code against a stub of the runtime, covering exact
price parsing, both verdicts on both sides, the tolerance edge, a fee inside the transaction, BTC with its own
decimals and markets, the exact minute asked of each venue, every no-ruling state (unsettled minute, missing or
reverted transaction, three assets, unlisted token, receive-only, uninvolved account, unread chain, RPCs that differ,
an unreachable RPC or venue, a candle for another minute, venues 200 bps apart), finality, per-trader rulings in one
transaction, paging, the policy, and a validator that reads a different fill. `--live` runs the same code on the three
real swaps against the real RPCs, OKX and Coinbase, and checks all 18 listed tokens' decimals on chain: 57 checks.

## Use it from another contract

```python
ruling = json.loads(gl.contract.get_at(gl.Address(TAPE)).view().ruling(8453, swap_tx_hash, trader))
if ruling.get("verdict") == "OFF_MARKET":
    ...  # refund the owner from the agent's bond, or score the executor down
```

`fair(chain, tx, trader)` is the one-line gate: true only for a final FAIR ruling.

## Reproduce

```bash
npm install
python tests/tape_rules.py --live
node scripts/verify.mjs
```

## Where it stops, plainly

- One-minute resolution. A fill anywhere inside that minute's range is fair, even if the market moved within the
  minute; the tolerance is fixed at 100 bps for every pair.
- ETH and BTC against dollar stablecoins only, on five chains. Anything else is `UNCHECKABLE`, never guessed.
- The trader must have paid exactly one token and received exactly one. Multi-leg strategies, native-coin legs and
  gas are outside it.
- OKX quotes in USDT and Coinbase in USD, and both stablecoins are taken at a dollar. A USDT depeg shows up as the two
  venues disagreeing, and the court then makes no ruling; a USDC depeg would not show in either venue.
- It rules on a fill; it moves no money. Contracts gate on `ruling()` and `fair()`.

## Licence

MIT.
