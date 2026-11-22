// Prove Tape on GenLayer Bradbury with real Base swaps. pchg, a stranger to every swap, asks the court; nothing else
// is needed: the fill comes from Base's receipts, the market from OKX's and Coinbase's public candles. Resumable
// (results/state.json).
//   node scripts/prove.mjs
import { Wallet } from 'ethers';
import fs from 'fs'; import os from 'os'; import path from 'path'; import url from 'url';
import { client, reader, fees } from './net.mjs';

const ROOT = path.join(path.dirname(url.fileURLToPath(import.meta.url)), '..');
const dep = JSON.parse(fs.readFileSync(path.join(ROOT, 'deployment.json'), 'utf8'));
const AT = dep.address;
const w = await Wallet.fromEncryptedJson(fs.readFileSync(path.join(os.homedir(), '.genlayer', 'keystores', 'pchg.json'), 'utf8'), process.env.PCHG || 'roster-test-chg-2026');
const gl = client(w.privateKey);
const anybody = reader();
const STATE = path.join(ROOT, 'results', 'state.json');
fs.mkdirSync(path.join(ROOT, 'results'), { recursive: true });
const st = fs.existsSync(STATE) ? JSON.parse(fs.readFileSync(STATE, 'utf8')) : {};
const save = () => fs.writeFileSync(STATE, JSON.stringify(st, null, 2));
const out = (st.transcript = []), checks = (st.checks = []), txs = st.txs || (st.txs = []);
const say = (l) => { console.log(l); out.push(l); save(); };
const check = (label, ok, detail = '') => { checks.push({ label, ok: !!ok, detail }); say(`  ${ok ? 'PASS' : 'FAIL'}  ${label}${detail ? ' | ' + detail : ''}`); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const read = async (fn, args) => anybody.readContract({ address: AT, functionName: fn, args });

const BASE = 8453;
const OFF_TX = '0xc64dd1516887f7944ce49a7383c3c1510f1ae9bcf733b8b812732c15c1a6c157';
// Real Base swaps, found by scanning WETH/USDC transfers and kept only where the account's whole fill is two tokens.
const CASES = [
  { key: 'FAIR_IN_BAND', tx: '0x21193672799778dada49e6680374c7f22f90ce4a17356fbabb11b3e4a5b36d90', trader: '0xb2cc224c1c9fee385f8ad6a55b4d94e92359dc59', want: 'FAIR', label: 'a purchase inside the minute\'s band' },
  { key: 'FAIR_TOLERANCE', tx: '0x3c30c2c2cab9cd4cc15497e318f0c0d62f05fa93f0dc389c4715733b11b8790e', trader: '0x4a7af46bc8abc00048c6c7920a1488daf568366c', want: 'FAIR', label: 'a purchase 65 bps over the band, within the tolerance' },
  { key: 'OFF_MARKET_FEE', tx: OFF_TX, trader: '0xd01d926e64b15eab812c9c26ecf99a340dc99277', want: 'OFF_MARKET', label: 'a sale at the pool price, with 3% taken by a fee in the same transaction' },
];
// No ruling may be stored for these.
const NONE = [
  { key: 'FEE_TAKER', chain: BASE, tx: OFF_TX, trader: '0xe799d6cc115dcdf6e67766dbb3d9c64914aed087', label: 'the fee taker in that transaction, who only received: not a swap' },
  { key: 'STRANGER', chain: BASE, tx: OFF_TX, trader: '0x' + '5a'.repeat(20), label: 'an account the swap never touched' },
  { key: 'UNKNOWN', chain: BASE, tx: '0x' + '00'.repeat(31) + '01', trader: '0xd01d926e64b15eab812c9c26ecf99a340dc99277', label: 'a hash Base never saw' },
  { key: 'OTHER_CHAIN', chain: 56, tx: OFF_TX, trader: '0xd01d926e64b15eab812c9c26ecf99a340dc99277', label: 'a chain the court does not read' },
];

async function rule(c) {
  if (st[c.key]?.final) return st[c.key];
  const chain = c.chain ?? BASE;
  const hash = await gl.writeContract({ address: AT, functionName: 'rule', args: [chain, c.tx, c.trader], value: 0n, fees: await fees(gl) });
  txs.push({ label: `rule ${c.key}`, hash }); save();
  let t;
  for (let i = 0; i < 90; i++) {
    await sleep(10000);
    t = await anybody.getTransaction({ hash });
    if (['FINALIZED', 'ACCEPTED', 'UNDETERMINED', 'CANCELED'].includes(t.statusName)) break;
  }
  const v = JSON.parse(await read('ruling', [chain, c.tx, c.trader]));
  st[c.key] = { hash, status: t?.statusName, result: t?.txExecutionResultName, ruling: v, final: t?.statusName === 'FINALIZED' || t?.statusName === 'ACCEPTED' };
  save();
  return st[c.key];
}

say('Tape, proven on GenLayer Bradbury with real Base swaps');
say(`  court ${AT} | asked by pchg ${w.address.toLowerCase()} | the fill: two Base RPCs; the market: OKX and Coinbase one-minute candles`);
say('\nrulings');
for (const c of CASES) {
  let r = await rule(c);
  if (r.ruling.exists === false) { say(`    ${c.key}: no ruling stored yet (${r.status}); asking again`); delete st[c.key]; r = await rule(c); }
  const v = r.ruling;
  check(`${c.label}: ${v.verdict}, wanted ${c.want}`, v.verdict === c.want, String(v.why || '').slice(0, 170));
}
say('\nno ruling where there is nothing to rule on');
for (const c of NONE) {
  const r = await rule(c);
  check(`${c.label}: nothing stored`, r.ruling.exists === false, `round ${r.status} ${r.result}`);
}

say('\nreads other contracts gate on');
const [f0, f1, f2] = CASES;
check('fair() is true for a FAIR fill', (await read('fair', [BASE, f0.tx, f0.trader])) === true);
check('fair() is false for an OFF_MARKET fill', (await read('fair', [BASE, f2.tx, f2.trader])) === false);
check('fair() is false for a fill never ruled', (await read('fair', [BASE, NONE[0].tx, NONE[0].trader])) === false);
const v2 = JSON.parse(await read('ruling', [BASE, f2.tx, f2.trader]));
check('the off-market ruling records what the trader really got, both candles, the band and the miss',
  v2.usd_amount === '24243217' && v2.okx.length === 3 && v2.coinbase.length === 3 && v2.miss_bps > 100 && v2.sources.fill.length === 2 && v2.sources.market.length === 2,
  `sold ${v2.base_amount} wei for ${v2.usd_amount} USDC units, band ${v2.low}..${v2.high}, miss ${v2.miss_bps} bps`);
check('the ruling carries an evidence digest anyone can recompute', /^[0-9a-f]{64}$/.test(v2.evidence));

say('\na final ruling is never changed');
const again = await gl.writeContract({ address: AT, functionName: 'rule', args: [BASE, f2.tx, f2.trader], value: 0n, fees: await fees(gl) });
txs.push({ label: 'rule again', hash: again }); save();
for (let i = 0; i < 60; i++) { await sleep(8000); const t = await anybody.getTransaction({ hash: again }); if (['FINALIZED', 'ACCEPTED'].includes(t.statusName)) break; }
const after = JSON.parse(await read('ruling', [BASE, f2.tx, f2.trader]));
check('asking again about a ruled fill leaves the ruling as it was', after.evidence === v2.evidence && after.ruled_at === v2.ruled_at);
const pg = JSON.parse(await read('page', [0, 10]));
check('the court lists exactly the three rulings', pg.total === 3, pg.items.map((i) => i.verdict).join(', '));

const passed = checks.filter((c) => c.ok).length;
say(`\n${passed}/${checks.length} checks passed`);
fs.writeFileSync(path.join(ROOT, 'results', 'proof.json'), JSON.stringify({ court: AT, checks, rulings: Object.fromEntries(CASES.map((c) => [c.key, st[c.key]?.ruling])), txs, transcript: out }, null, 2));
process.exit(passed === checks.length ? 0 : 1);
