// Deploy Tape on GenLayer Bradbury. It takes no arguments: everything it rules on comes from the chains and the exchanges.
//   node scripts/deploy.mjs
import { Wallet } from 'ethers';
import fs from 'fs'; import os from 'os'; import path from 'path'; import url from 'url';
import { client, fees, EXPLORER } from './net.mjs';

const ROOT = path.join(path.dirname(url.fileURLToPath(import.meta.url)), '..');
const OUT = path.join(ROOT, 'deployment.json');
const w = await Wallet.fromEncryptedJson(fs.readFileSync(path.join(os.homedir(), '.genlayer', 'keystores', 'pchg.json'), 'utf8'), process.env.PCHG || 'roster-test-chg-2026');
const c = client(w.privateKey);
const code = fs.readFileSync(path.join(ROOT, 'contracts', 'tape.py'), 'utf8');
const hash = await c.deployContract({ code, args: [], fees: await fees(c) });
console.log('deploy tx', hash);
for (let i = 0; i < 90; i++) {
  await new Promise((r) => setTimeout(r, 10000));
  const tx = await c.getTransaction({ hash });
  if (['ACCEPTED', 'FINALIZED'].includes(tx.statusName)) {
    if (tx.txExecutionResultName !== 'FINISHED_WITH_RETURN') { console.error('deploy failed', tx.txExecutionResultName); process.exit(1); }
    const address = tx.txDataDecoded?.contractAddress || tx.recipient;
    fs.writeFileSync(OUT, JSON.stringify({ network: 'genlayer testnet bradbury phase 3', address, tx: hash, deployer: w.address, deployed_at: new Date().toISOString(), explorer: EXPLORER + '/address/' + address }, null, 2));
    console.log('Tape', address);
    process.exit(0);
  }
}
console.error('deploy did not finish'); process.exit(1);
