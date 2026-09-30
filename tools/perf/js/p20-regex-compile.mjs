// JS-P20: per-call regex compile() overhead (flag loop, ASCII scan for `i`, key concat, Map lookup).
import { sel, bench } from './lib.mjs';
const rows = Array.from({ length: 300000 }, (_, i) => ({ email: 'user' + i + '@example.org' }));
const p = sel.compile('COUNT(FILTER(ROWS, RMATCH(\'^[a-z0-9]+@[a-z.]+$\', _["email"])))');
bench('P20 RMATCH literal over 300k rows', () => p.run({ ROWS: rows }).scalar, { reps: 7, warm: 2 });
const pi = sel.compile('COUNT(FILTER(ROWS, RMATCH(\'^USER[0-9]+@EXAMPLE\\.ORG$\', _["email"], "i")))');
bench('P20 RMATCH with i flag over 300k rows', () => pi.run({ ROWS: rows }).scalar, { reps: 7, warm: 2 });
const ctl = sel.compile('COUNT(FILTER(ROWS, LEN(_["email"]) > 3))');
bench('P20 control (no regex) over 300k rows', () => ctl.run({ ROWS: rows }).scalar, { reps: 7, warm: 2 });
