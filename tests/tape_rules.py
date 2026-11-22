"""Tape's rules, exercised through the real contract against a stub of the GenLayer runtime.

    python tests/tape_rules.py           unit checks: receipts, blocks and candles served from memory
    python tests/tape_rules.py --live    the same contract code against the real RPCs and the real OKX and Coinbase
                                         APIs, on real Base swaps (nothing is written to any chain)
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stub  # noqa: E402

TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
WETH = "0x4200000000000000000000000000000000000006"
USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
CBBTC = "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf"
JUNK = "0x" + "9" * 40
TRADER = "0x" + "a" * 40
POOL = "0x" + "b" * 40
FEE = "0x" + "c" * 40
TX = "0x" + "1" * 64
A, B = "https://base-rpc.publicnode.com", "https://base.drpc.org"
MINUTE = 1795300000 - 1795300000 % 60
NOW = MINUTE + 3600

RESULTS = []


def check_(label, cond):
    RESULTS.append((label, bool(cond)))
    print(("  ok   " if cond else "  FAIL ") + label)


def log(token, frm, to, amount):
    return {"address": token, "topics": [TRANSFER, "0x" + "0" * 24 + frm[2:], "0x" + "0" * 24 + to[2:]], "data": hex(amount)}


def receipt(logs, ok=True, block=1000):
    return {"status": "0x1" if ok else "0x0", "blockNumber": hex(block), "logs": logs}


def sell(eth_wei, usdc_units, extra=()):
    return [log(WETH, TRADER, POOL, eth_wei), log(USDC, POOL, TRADER, usdc_units)] + list(extra)


def buy(eth_wei, usdc_units):
    return [log(USDC, TRADER, POOL, usdc_units), log(WETH, POOL, TRADER, eth_wei)]


def okx(low, high, close, minute=MINUTE):
    return {"code": "0", "msg": "", "data": [[str(minute * 1000), "1700", high, low, close, "1", "1", "1", "1"]]}


def coinbase(low, high, close, minute=MINUTE):
    return [[minute + 60, 1, 2, 1, 2, 1], [minute, low, high, 1700.0, close, 12.5]]


def world(m, gl, rcpt, block_time=MINUTE + 17, ok_candle=("1700.00", "1710.00", "1705.00"), cb_candle=(1699.5, 1709.0, 1705.2),
          second=None):
    web = gl.nondet.web
    web.rpc.clear(); web.pages.clear(); web.calls.clear()
    for url, r in ((A, rcpt), (B, rcpt if second is None else second)):
        web.rpc[(url, "eth_getTransactionReceipt", TX)] = r
        if isinstance(r, dict):
            web.rpc[(url, "eth_getBlockByNumber", r["blockNumber"])] = {"timestamp": hex(block_time)}
    if ok_candle is not None:
        web.pages[m.OKX + "ETH-USDT"] = (200, okx(*ok_candle))
    if cb_candle is not None:
        web.pages[m.COINBASE + "ETH-USD/"] = (200, coinbase(*cb_candle))


def run(m, gl, chain=8453, tx=TX, trader=TRADER):
    return m.check(chain, tx, trader, NOW)


def main():
    m, gl = stub.load()
    E = 10 ** 18

    print("prices")
    check_("a decimal price is read exactly in 1e-8 units", m.price_of("1705.12") == 170512000000 and m.price_of("2") == 200000000)
    check_("a price with junk, a sign or an exponent is refused", m.price_of("1e3") == -1 and m.price_of("-5") == -1 and m.price_of("") == -1)

    print("\nverdicts")
    world(m, gl, receipt(sell(1 * E, 1702_000000)))
    got = run(m, gl)
    check_("a sale inside the band is FAIR with no miss", got["verdict"] == "FAIR" and got["miss_bps"] == 0)
    check_("the band is the lowest low and the highest high of the two exchanges", got["low"] == m.price_of("1699.5") and got["high"] == m.price_of("1710.00"))
    world(m, gl, receipt(sell(1 * E, 1690_000000)))
    got = run(m, gl)
    check_("a sale 55 bps under the low is FAIR, within the tolerance", got["verdict"] == "FAIR" and got["miss_bps"] == 55)
    world(m, gl, receipt(sell(1 * E, 1650_000000)))
    got = run(m, gl)
    check_("a sale 291 bps under the low is OFF_MARKET", got["verdict"] == "OFF_MARKET" and got["miss_bps"] == 291)
    world(m, gl, receipt(buy(1 * E, 1760_000000)))
    got = run(m, gl)
    check_("a purchase 292 bps over the high is OFF_MARKET", got["verdict"] == "OFF_MARKET" and got["side"] == "BUY" and got["miss_bps"] == 292)
    world(m, gl, receipt(buy(2 * E, 3400_000000)))
    check_("a purchase under the high is FAIR, whatever the size", run(m, gl)["verdict"] == "FAIR")
    world(m, gl, receipt(sell(1 * E, 1702_000000, [log(USDC, TRADER, FEE, 60_000000)])))
    got = run(m, gl)
    check_("a fee the trader pays inside the same transaction counts against the fill",
           got["verdict"] == "OFF_MARKET" and got["usd_amount"] == str(1642_000000))
    world(m, gl, receipt([log(CBBTC, TRADER, POOL, 10 ** 8), log(USDC, POOL, TRADER, 90000_000000)]))
    gl.nondet.web.pages[m.OKX + "BTC-USDT"] = (200, okx("89900", "90200", "90000"))
    gl.nondet.web.pages[m.COINBASE + "BTC-USD/"] = (200, coinbase(89950, 90100, 90010))
    got = run(m, gl)
    check_("BTC is priced against BTC markets, with its own decimals", got["verdict"] == "FAIR" and got["asset"] == "BTC")
    check_("and the court asked OKX and Coinbase for that exact minute",
           any("instId=BTC-USDT&after=" + str((MINUTE + 60) * 1000) in u for _, u in gl.nondet.web.calls)
           and any("BTC-USD/candles?granularity=60&start=" + str(MINUTE) in u for _, u in gl.nondet.web.calls))

    print("\nno ruling")
    world(m, gl, receipt(sell(1 * E, 1702_000000)), block_time=NOW - 30)
    check_("a swap whose minute has not settled is PENDING, and no exchange is asked yet",
           run(m, gl)["verdict"] == "PENDING" and not any(k == "GET" for k, _ in gl.nondet.web.calls))
    world(m, gl, None)
    check_("a transaction the chain does not show is PENDING, never a verdict", run(m, gl)["verdict"] == "PENDING")
    world(m, gl, receipt(sell(1 * E, 1702_000000), ok=False))
    check_("a reverted transaction is UNCHECKABLE", run(m, gl)["verdict"] == "UNCHECKABLE")
    world(m, gl, receipt(sell(1 * E, 1702_000000, [log(JUNK, POOL, TRADER, 5)])))
    check_("a trader whose tokens moved in three assets is UNCHECKABLE", run(m, gl)["verdict"] == "UNCHECKABLE")
    world(m, gl, receipt([log(JUNK, TRADER, POOL, 5), log(USDC, POOL, TRADER, 5)]))
    check_("a token the court does not list is never priced", run(m, gl)["verdict"] == "UNCHECKABLE")
    world(m, gl, receipt([log(WETH, POOL, TRADER, E), log(USDC, POOL, TRADER, 5)]))
    check_("receiving both tokens is not a swap", run(m, gl)["verdict"] == "UNCHECKABLE")
    world(m, gl, receipt(sell(1 * E, 1702_000000)))
    got = run(m, gl, trader="0x" + "d" * 40)
    check_("an account the swap never touched is UNCHECKABLE: naming someone else's swap proves nothing about you", got["verdict"] == "UNCHECKABLE")
    check_("a chain the court does not read is UNCHECKABLE", run(m, gl, chain=56)["verdict"] == "UNCHECKABLE")
    world(m, gl, receipt(sell(1 * E, 1702_000000)), second=receipt(sell(1 * E, 1600_000000)))
    check_("two RPCs that show different receipts are SPLIT", run(m, gl)["verdict"] == "SPLIT")
    world(m, gl, receipt(sell(1 * E, 1702_000000)))
    gl.nondet.web.rpc[(B, "eth_getTransactionReceipt", TX)] = "down"
    check_("an RPC that cannot be reached is UNREADABLE", run(m, gl)["verdict"] == "UNREADABLE")
    world(m, gl, receipt(sell(1 * E, 1702_000000)), cb_candle=None)
    check_("an exchange that cannot be read is UNREADABLE", run(m, gl)["verdict"] == "UNREADABLE")
    world(m, gl, receipt(sell(1 * E, 1702_000000)), ok_candle=("1700.00", "1710.00", "1705.00"))
    gl.nondet.web.pages[m.OKX + "ETH-USDT"] = (200, okx("1700.00", "1710.00", "1705.00", minute=MINUTE - 60))
    check_("a candle for another minute is never used", run(m, gl)["verdict"] == "UNREADABLE")
    world(m, gl, receipt(sell(1 * E, 1702_000000)), cb_candle=(1600.0, 1650.0, 1640.0))
    check_("two exchanges more than 200 bps apart are SPLIT: the market itself is in doubt", run(m, gl)["verdict"] == "SPLIT")

    print("\nthe court")
    c = stub.fresh(m)
    world(m, gl, receipt(sell(1 * E, 1650_000000)))
    m._now = lambda: NOW
    out = json.loads(c.rule(8453, TX.upper().replace("0X", "0x"), TRADER.upper().replace("0X", "0x")))
    check_("rule() stores an OFF_MARKET ruling, keyed by chain, hash and trader, case-insensitively", out["ok"] and out["verdict"] == "OFF_MARKET")
    rec = json.loads(c.ruling(8453, TX, TRADER))
    check_("the ruling records the fill, both candles, the miss and both kinds of source",
           rec["usd_amount"] == "1650000000" and rec["okx"] and rec["coinbase"] and rec["miss_bps"] == 291
           and len(rec["sources"]["fill"]) == 2 and len(rec["sources"]["market"]) == 2)
    check_("fair() is false for it", c.fair(8453, TX, TRADER) is False)
    world(m, gl, receipt(sell(1 * E, 1702_000000)))
    again = json.loads(c.rule(8453, TX, TRADER))
    check_("a final ruling is never changed, even if the sources change", not again["ok"] and json.loads(c.ruling(8453, TX, TRADER))["verdict"] == "OFF_MARKET")
    other = "0x" + "e" * 40
    world(m, gl, receipt([log(WETH, other, POOL, E), log(USDC, POOL, other, 1702_000000)]))
    json.loads(c.rule(8453, TX, other))
    check_("another trader in the same transaction gets a ruling of their own", c.fair(8453, TX, other) is True)
    world(m, gl, None)
    out = json.loads(c.rule(8453, "0x" + "2" * 64, TRADER))
    check_("a call with no ruling stores nothing", not out["ok"] and len(c.ids) == 2)
    check_("malformed input is refused before any source is read", not json.loads(c.rule(8453, "0x12", TRADER))["ok"])
    check_("ruling() of a fill never ruled says it does not exist", json.loads(c.ruling(8453, "0x" + "3" * 64, TRADER)) == {"exists": False})
    page = json.loads(c.page(0, 10))
    check_("page() lists newest first", page["total"] == 2 and page["items"][0]["trader"] == other)
    pol = json.loads(c.policy())
    check_("the policy names both sources, the tolerance, and that no model is asked",
           pol["tolerance_bps"] == 100 and "OKX" in pol["market"] and "Coinbase" in pol["market"] and pol["model"].startswith("none"))

    print("\nthe round")
    world(m, gl, receipt(sell(1 * E, 1702_000000)))
    lead = run(m, gl)
    world(m, gl, receipt(sell(1 * E, 1702_000001)))
    mine = run(m, gl)
    check_("a validator that reads a different fill reaches a different evidence digest, so the round fails",
           lead["verdict"] == mine["verdict"] and lead["evidence"] != mine["evidence"])
    world(m, gl, receipt(sell(1 * E, 1702_000000)))
    check_("the same sources give the same digest every time", run(m, gl)["evidence"] == lead["evidence"])

    if "--live" in sys.argv:
        live()

    failed = [l for l, ok in RESULTS if not ok]
    print("\n%d checks, %d failed" % (len(RESULTS), len(failed)))
    sys.exit(1 if failed else 0)


LIVE = [
    ("0x21193672799778dada49e6680374c7f22f90ce4a17356fbabb11b3e4a5b36d90", "0xb2cc224c1c9fee385f8ad6a55b4d94e92359dc59", "FAIR"),
    ("0x3c30c2c2cab9cd4cc15497e318f0c0d62f05fa93f0dc389c4715733b11b8790e", "0x4a7af46bc8abc00048c6c7920a1488daf568366c", "FAIR"),
    ("0xc64dd1516887f7944ce49a7383c3c1510f1ae9bcf733b8b812732c15c1a6c157", "0xd01d926e64b15eab812c9c26ecf99a340dc99277", "OFF_MARKET"),
]


def live():
    print("\nlive: real Base swaps, real RPCs, real OKX and Coinbase")
    m, gl = stub.load(live=True)
    now = m._now()
    for tx, trader, want in LIVE:
        got = m.check(8453, tx, trader, now)
        check_("%s: %s, wanted %s | %s" % (tx[:10], got.get("verdict"), want, got.get("why", "")), got.get("verdict") == want)
    for chain, table in m.TOKENS.items():
        for token, (_, dec) in table.items():
            _, url, _ = m.CHAINS[chain]
            res = gl.nondet.web.post(url, body=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                                                          "params": [{"to": token, "data": "0x313ce567"}, "latest"]}).encode(),
                                     headers={"Content-Type": "application/json"})
            got = m.hex_int(json.loads(res.body).get("result"))
            check_("chain %d token %s has %d decimals on chain" % (chain, token[:10], dec), got == dec)


if __name__ == "__main__":
    main()
