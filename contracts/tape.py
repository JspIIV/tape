# v0.4.0
# { "Depends": "py-genlayer:kzybg95x1gn21dw72m7hyg0maaap04n4nx55j9yt6fkdqxtz6bz0" }
"""Tape: Internet Court rulings on whether an agent's swap filled at the market price.

An agent swaps on chain and its owner, or the agent it traded for, says the fill was bad. Today nothing on chain can
tell a fair fill from a sandwiched or mispriced one: the price the market showed at that minute lives on exchanges,
not on the chain. Tape makes it a ruling.

rule(chain_id, tx_hash, trader), open to anybody, names the swap and the account whose fill is judged. Everything
else comes from sources nobody in the dispute controls:

  the fill     the transaction's receipt on its own chain, read from two independent public RPCs that must agree:
               what the trader paid and what it received, summed in code from its ERC-20 Transfer events, and the
               block's timestamp
  the market   the one-minute candle for that minute from two exchanges, OKX and Coinbase, read from their public
               APIs: the lowest low and the highest high of the two make the band the market traded in

and rules:

  FAIR        the fill is inside the band, or outside it by no more than the tolerance (100 bps, for pool fees and
              price impact)
  OFF_MARKET  the trader sold below the band's low, or bought above its high, by more than the tolerance

A swap too recent for its minute to have closed, a transaction that is not a two-token swap of a priced asset
against a dollar stablecoin for that account, a chain the court does not read, two RPCs that disagree, or two
exchanges that disagree with each other: no ruling, and the same call can be made again.

No model is asked anything: the verdict is integer arithmetic over two independent sources for each side, agreed by
every validator. Other contracts read it with ruling(chain_id, tx_hash, trader) and fair(...).
"""

import genlayer as gl
import hashlib
import json
import re

TOLERANCE_BPS = 100
SETTLE_SECONDS = 180  # a minute's candle is final once the minute has closed; wait a little longer
SCALE = 10 ** 8       # prices are integers in units of 1e-8 dollars

# chain id -> (name, first RPC, second RPC): two operators, so one bad answer cannot decide a case.
CHAINS = {
    1: ("ethereum", "https://ethereum-rpc.publicnode.com", "https://eth.drpc.org"),
    10: ("optimism", "https://mainnet.optimism.io", "https://optimism.drpc.org"),
    137: ("polygon", "https://polygon.drpc.org", "https://1rpc.io/matic"),
    8453: ("base", "https://base-rpc.publicnode.com", "https://base.drpc.org"),
    42161: ("arbitrum", "https://arb1.arbitrum.io/rpc", "https://arbitrum.drpc.org"),
}
# chain id -> token -> (asset, decimals). Only these tokens are priced: a token the court cannot price is never guessed.
TOKENS = {
    1: {
        "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": ("ETH", 18),
        "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": ("BTC", 8),
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USD", 6),
        "0xdac17f958d2ee523a2206206994597c13d831ec7": ("USD", 6),
    },
    10: {
        "0x4200000000000000000000000000000000000006": ("ETH", 18),
        "0x0b2c639c533813f4aa9d7837caf62653d097ff85": ("USD", 6),
        "0x94b008aa00579c1307b0ef2c499ad98a8ce58e58": ("USD", 6),
    },
    137: {
        "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619": ("ETH", 18),
        "0x1bfd67037b42cf73acf2047067bd4f2c47d9bfd6": ("BTC", 8),
        "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359": ("USD", 6),
        "0xc2132d05d31c914a87c6611c10748aeb04b58e8f": ("USD", 6),
    },
    8453: {
        "0x4200000000000000000000000000000000000006": ("ETH", 18),
        "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf": ("BTC", 8),
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": ("USD", 6),
    },
    42161: {
        "0x82af49447d8a07e3bd95bd0d56f35241523fbab1": ("ETH", 18),
        "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f": ("BTC", 8),
        "0xaf88d065e77c8cc2239327c5edb3a432268e5831": ("USD", 6),
        "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9": ("USD", 6),
    },
}
# asset -> (OKX instrument, Coinbase product)
MARKETS = {"ETH": ("ETH-USDT", "ETH-USD"), "BTC": ("BTC-USDT", "BTC-USD")}
OKX = "https://www.okx.com/api/v5/market/history-candles?bar=1m&limit=1&instId="
COINBASE = "https://api.exchange.coinbase.com/products/"
VENUE_GAP_BPS = 200  # two exchanges whose closes differ by more than this disagree about the market
HEADERS = {"User-Agent": "tape-court/1.0", "Accept": "application/json"}

TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
FAIR, OFF_MARKET = "FAIR", "OFF_MARKET"
FINAL = (FAIR, OFF_MARKET)
PENDING, UNCHECKABLE, SPLIT, UNREADABLE = "PENDING", "UNCHECKABLE", "SPLIT", "UNREADABLE"

HASH = re.compile(r"^0x[0-9a-f]{64}$")
ADDR = re.compile(r"^0x[0-9a-f]{40}$")
DECIMAL = re.compile(r"^[0-9]+(\.[0-9]+)?$")


def _now() -> int:
    from datetime import datetime, timezone
    return int(datetime.now(timezone.utc).timestamp())


def _b(value) -> bytes:
    return bytes(value) if isinstance(value, (bytes, bytearray, memoryview)) else str(value).encode("utf-8")


def _json(res):
    """(status, parsed JSON or None). Numbers stay text, so every validator reads exactly the same digits."""
    status = int(getattr(res, "status", 0) or 0)
    try:
        return status, json.loads(_b(getattr(res, "body", b"") or b"").decode("utf-8"), parse_float=str, parse_int=str)
    except Exception:
        return status, None


def tx_of(text) -> str:
    t = str(text if text is not None else "").strip().lower()
    return t if HASH.match(t) else ""


def addr_of(text) -> str:
    t = str(text if text is not None else "").strip().lower()
    return t if ADDR.match(t) else ""


def price_of(text) -> int:
    """A decimal price as an integer in 1e-8 units, or -1. Exact: no float ever touches a price."""
    t = str(text if text is not None else "").strip()
    if not DECIMAL.match(t):
        return -1
    whole, _, frac = t.partition(".")
    frac = (frac + "0" * 8)[:8]
    return int(whole) * SCALE + int(frac)


def hex_int(text) -> int:
    try:
        return int(str(text), 16)
    except Exception:
        return -1


def case_key(chain_id: int, tx: str, trader: str) -> str:
    return str(chain_id) + ":" + tx + ":" + trader


# ------------------------------------------------------------------ the fill: receipt and block, from two RPCs

def read_receipt(receipt, trader: str) -> dict:
    """{found, ok, block, flows: {token: net amount for the trader}} from an eth_getTransactionReceipt result."""
    if not isinstance(receipt, dict):
        return {"found": False}
    flows = {}
    for log in receipt.get("logs") or []:
        topics = (log.get("topics") or []) if isinstance(log, dict) else []
        if len(topics) != 3 or str(topics[0]).lower() != TRANSFER:
            continue
        amount = hex_int(log.get("data", "0x0"))
        if amount < 0:
            continue
        token = addr_of(log.get("address"))
        sender, to = "0x" + str(topics[1]).lower()[-40:], "0x" + str(topics[2]).lower()[-40:]
        if sender == trader:
            flows[token] = flows.get(token, 0) - amount
        if to == trader:
            flows[token] = flows.get(token, 0) + amount
    return {"found": True, "ok": str(receipt.get("status", "")).lower() == "0x1",
            "block": hex_int(receipt.get("blockNumber")),
            "flows": {t: a for t, a in sorted(flows.items()) if a != 0}}


def _rpc(url: str, method: str, params: list):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    try:
        status, doc = _json(gl.nondet.web.post(url, body=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}))
    except Exception:
        return None, False
    if status >= 400 or not isinstance(doc, dict) or "error" in doc:
        return None, False
    return doc.get("result"), True


def fetch_fill(url: str, tx: str, trader: str):
    """The receipt as the trader saw it, plus its block's timestamp; None if this RPC cannot be read."""
    receipt, ok = _rpc(url, "eth_getTransactionReceipt", [tx])
    if not ok:
        return None
    got = read_receipt(receipt, trader)
    if got.get("found") and got.get("block", -1) >= 0:
        block, ok = _rpc(url, "eth_getBlockByNumber", [hex(got["block"]), False])
        if not ok or not isinstance(block, dict):
            return None
        got["time"] = hex_int(block.get("timestamp"))
    return got


def swap_of(chain_id: int, fill: dict) -> dict:
    """The trade the flows describe: one priced asset against one dollar stablecoin, or a reason it is not one."""
    flows = fill.get("flows") or {}
    table = TOKENS.get(chain_id, {})
    if len(flows) != 2:
        return {"why": "the trader's tokens moved in " + str(len(flows)) + " assets in this transaction; a ruling needs exactly two, one paid and one received."}
    (t1, a1), (t2, a2) = sorted(flows.items())
    if t1 not in table or t2 not in table:
        return {"why": "the court prices only the tokens it lists for this chain; this swap moves " + ", ".join(t for t in (t1, t2) if t not in table) + "."}
    if (a1 > 0) == (a2 > 0):
        return {"why": "the trader either paid both tokens or received both; that is not a swap."}
    s1, s2 = table[t1][0], table[t2][0]
    if (s1 == "USD") == (s2 == "USD"):
        return {"why": "a ruling needs one priced asset (" + ", ".join(sorted(MARKETS)) + ") against a dollar stablecoin."}
    base_t, base_a, usd_t, usd_a = (t2, a2, t1, a1) if s1 == "USD" else (t1, a1, t2, a2)
    asset, base_dec = table[base_t]
    usd_dec = table[usd_t][1]
    side = "SELL" if base_a < 0 else "BUY"
    base_amt, usd_amt = abs(base_a), abs(usd_a)
    # dollars per whole unit of the asset, in 1e-8 units, floored: integers only, so every validator gets the same digits
    price = usd_amt * (10 ** base_dec) * SCALE // (base_amt * (10 ** usd_dec))
    return {"asset": asset, "side": side, "base_token": base_t, "base_amount": str(base_amt),
            "usd_token": usd_t, "usd_amount": str(usd_amt), "price": price}


# ------------------------------------------------------------------ the market: two exchanges' candles

def read_okx(doc, minute: int):
    """(low, high, close) of OKX's candle that opens at `minute`, or None."""
    if not isinstance(doc, dict) or str(doc.get("code", "")) != "0":
        return None
    rows = doc.get("data") or []
    if not rows or not isinstance(rows[0], list) or len(rows[0]) < 5:
        return None
    row = rows[0]
    if str(row[0]) != str(minute * 1000):
        return None
    low, high, close = price_of(row[3]), price_of(row[2]), price_of(row[4])
    return (low, high, close) if min(low, high, close) > 0 and low <= high else None


def read_coinbase(doc, minute: int):
    """(low, high, close) of Coinbase's candle that opens at `minute`, or None. Rows are [time, low, high, open, close, volume]."""
    if not isinstance(doc, list):
        return None
    for row in doc:
        if isinstance(row, list) and len(row) >= 5 and str(row[0]) == str(minute):
            low, high, close = price_of(row[1]), price_of(row[2]), price_of(row[4])
            return (low, high, close) if min(low, high, close) > 0 and low <= high else None
    return None


def fetch_market(asset: str, minute: int):
    okx_id, cb_id = MARKETS[asset]
    out = []
    for url, reader in ((OKX + okx_id + "&after=" + str((minute + 60) * 1000), read_okx),
                        (COINBASE + cb_id + "/candles?granularity=60&start=" + str(minute) + "&end=" + str(minute + 60), read_coinbase)):
        try:
            status, doc = _json(gl.nondet.web.get(url, headers=HEADERS))
        except Exception:
            status, doc = 0, None
        out.append(reader(doc, minute) if status < 400 else None)
    return out[0], out[1]


def judge(chain_id: int, first, second, okx, coinbase, now: int) -> dict:
    """The verdict from the two readings of the fill and the two candles. Pure: unit tested."""
    if chain_id not in CHAINS:
        return {"verdict": UNCHECKABLE, "why": "the court reads no chain with id " + str(chain_id) + "."}
    if first is None or second is None:
        return {"verdict": UNREADABLE}
    if first != second:
        return {"verdict": SPLIT, "why": "the two RPCs do not show the same receipt yet."}
    fill = first
    if not fill.get("found"):
        return {"verdict": PENDING, "why": "the chain does not show this transaction yet; a pruned or lagging node is not evidence, so ask again later."}
    if not fill.get("ok"):
        return {"verdict": UNCHECKABLE, "why": "the transaction reverted, so nothing was swapped."}
    swap = swap_of(chain_id, fill)
    if "why" in swap:
        return {"verdict": UNCHECKABLE, "why": swap["why"]}
    when = fill.get("time", -1)
    if when <= 0:
        return {"verdict": UNREADABLE}
    minute = when - when % 60
    base = dict(swap, block=fill["block"], time=when, minute=minute)
    if now - minute < SETTLE_SECONDS:
        return dict(base, verdict=PENDING, why="the minute of this swap has not settled on the exchanges yet; ask again in a few minutes.")
    if okx is None or coinbase is None:
        return dict(base, verdict=UNREADABLE, why="an exchange's candle for that minute could not be read.")
    gap = abs(okx[2] - coinbase[2]) * 10000 // min(okx[2], coinbase[2])
    if gap > VENUE_GAP_BPS:
        return dict(base, verdict=SPLIT, why="OKX and Coinbase closed that minute " + str(gap) + " bps apart, so the market itself is in doubt.")
    low, high, price = min(okx[0], coinbase[0]), max(okx[1], coinbase[1]), swap["price"]
    if swap["side"] == "SELL":
        miss = (low - price) * 10000 // low if price < low else 0
    else:
        miss = (price - high) * 10000 // high if price > high else 0
    base.update({"okx": [str(v) for v in okx], "coinbase": [str(v) for v in coinbase], "low": low, "high": high,
                 "miss_bps": miss, "tolerance_bps": TOLERANCE_BPS})
    shown = _usd(price) + " per " + swap["asset"]
    band = _usd(low) + " to " + _usd(high)
    if miss <= TOLERANCE_BPS:
        return dict(base, verdict=FAIR, why="The trader " + ("sold" if swap["side"] == "SELL" else "bought") + " at " + shown
                    + "; the market traded " + band + " that minute" + (", within the band." if miss == 0 else ", " + str(miss) + " bps outside it, within the " + str(TOLERANCE_BPS) + " bps tolerance."))
    return dict(base, verdict=OFF_MARKET, why="The trader " + ("sold" if swap["side"] == "SELL" else "bought") + " at " + shown
                + "; the market traded " + band + " that minute, " + str(miss) + " bps " + ("below its low." if swap["side"] == "SELL" else "above its high."))


def _usd(units: int) -> str:
    return "$" + str(units // SCALE) + "." + str(units % SCALE).rjust(8, "0")[:2]


def evidence(chain_id: int, tx: str, trader: str, got: dict) -> str:
    """A digest of what the round agreed: anyone can recompute it from the chain and the two exchanges."""
    line = "|".join([str(chain_id), tx, trader, got.get("verdict", ""), str(got.get("block", "")), str(got.get("time", "")),
                     got.get("side", ""), got.get("base_token", ""), got.get("base_amount", ""), got.get("usd_token", ""),
                     got.get("usd_amount", ""), ",".join(got.get("okx", [])), ",".join(got.get("coinbase", [])),
                     str(got.get("miss_bps", ""))])
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def check(chain_id: int, tx: str, trader: str, now: int) -> dict:
    """The fill from both RPCs, then both exchanges' candles for its minute. One deterministic answer per round."""
    first = second = okx = coinbase = None
    if chain_id in CHAINS:
        _, a, b = CHAINS[chain_id]
        first, second = fetch_fill(a, tx, trader), fetch_fill(b, tx, trader)
        if first is not None and first == second and first.get("found") and first.get("ok") and first.get("time", -1) > 0:
            swap = swap_of(chain_id, first)
            minute = first["time"] - first["time"] % 60
            if "why" not in swap and now - minute >= SETTLE_SECONDS:
                okx, coinbase = fetch_market(swap["asset"], minute)
    got = judge(chain_id, first, second, okx, coinbase, now)
    got["evidence"] = evidence(chain_id, tx, trader, got)
    return got


class Tape(gl.contract.Contract):
    # "chain:tx:trader" -> ruling JSON
    rulings: gl.storage.TreeMap[str, str]
    ids: gl.storage.DynArray[str]

    def __init__(self) -> None:
        pass

    @gl.public.write
    def rule(self, chain_id: int, tx_hash: str, trader: str) -> str:
        """Rule on the trader's fill in tx_hash. Open to anybody; a final ruling is never changed."""
        chain, tx, who = int(chain_id), tx_of(tx_hash), addr_of(trader)
        if not tx or not who:
            return json.dumps({"ok": False, "error": "give the swap's hash as 0x and 64 hex characters and the trader as 0x and 40"})
        key = case_key(chain, tx, who)
        if self.rulings.get(key, None) is not None:
            return json.dumps({"ok": False, "error": "the court has already ruled on this fill"})
        now = _now()

        def lead() -> dict:
            return check(chain, tx, who, now)

        def agree(leaders_res) -> bool:
            if not isinstance(leaders_res, gl.vm.Return) or not isinstance(leaders_res.calldata, dict):
                return False
            mine, theirs = check(chain, tx, who, now), leaders_res.calldata
            return all(mine.get(k) == theirs.get(k) for k in ("verdict", "evidence"))

        got = gl.vm.run_nondet(lead, agree)
        verdict = str(got.get("verdict", "")) if isinstance(got, dict) else ""
        if verdict not in FINAL:
            if verdict in (PENDING, UNCHECKABLE, SPLIT):
                return json.dumps({"ok": False, "verdict": verdict, "error": str(got.get("why", "")) + " Nothing changed."})
            return json.dumps({"ok": False, "verdict": verdict, "error": "the chain or an exchange could not be read; nothing changed."})

        record = dict(got)
        record.update({"chain": chain, "tx": tx, "trader": who, "ruled_at": now,
                       "by": gl.message.sender_address.as_hex.lower(),
                       "sources": {"fill": list(CHAINS[chain][1:]),
                                   "market": [OKX + MARKETS[got["asset"]][0], COINBASE + MARKETS[got["asset"]][1] + "/candles"]}})
        self.ids.append(key)
        self.rulings[key] = json.dumps(record)
        return json.dumps({"ok": True, "verdict": verdict, "why": got.get("why", ""), "evidence": got.get("evidence", "")})

    # ------------------------------------------------------------------ reads

    @gl.public.view
    def ruling(self, chain_id: int, tx_hash: str, trader: str) -> str:
        """The ruling on a fill, or {"exists": false}. What other contracts gate on."""
        stored = self.rulings.get(case_key(int(chain_id), tx_of(tx_hash), addr_of(trader)), None)
        return stored if stored is not None else json.dumps({"exists": False})

    @gl.public.view
    def fair(self, chain_id: int, tx_hash: str, trader: str) -> bool:
        """True only for a final FAIR ruling."""
        stored = self.rulings.get(case_key(int(chain_id), tx_of(tx_hash), addr_of(trader)), None)
        return stored is not None and json.loads(stored).get("verdict") == FAIR

    @gl.public.view
    def policy(self) -> str:
        return json.dumps({
            "fill": "the receipt and block, from two independent RPCs that must agree; the trader's net ERC-20 flows",
            "market": "the one-minute candle of the swap's minute from OKX and from Coinbase; band = lowest low to highest high",
            "verdicts": list(FINAL), "no_ruling": [PENDING, UNCHECKABLE, SPLIT, UNREADABLE],
            "tolerance_bps": TOLERANCE_BPS, "venue_gap_bps": VENUE_GAP_BPS, "settle_seconds": SETTLE_SECONDS,
            "chains": {str(k): v[0] for k, v in CHAINS.items()},
            "assets": {k: list(v) for k, v in MARKETS.items()},
            "model": "none: every verdict is integer arithmetic over the sources",
        })

    @gl.public.view
    def page(self, start: int, count: int) -> str:
        total = len(self.ids)
        begin = max(0, int(start))
        want = min(50, max(1, int(count)))
        out = []
        pos = total - 1 - begin
        while pos >= 0 and len(out) < want:
            out.append(json.loads(self.rulings[self.ids[pos]]))
            pos -= 1
        return json.dumps({"total": total, "items": out})
