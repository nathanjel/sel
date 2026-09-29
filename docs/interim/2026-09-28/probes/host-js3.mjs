import { Value } from '../wt2/js/src/sel.mjs';
const T = (name, f) => { let r; try { r = 'OK ' + f().dump().slice(0, 60); } catch (e) { r = 'ERR ' + (e.code || e.constructor.name) + ' ' + String(e.message).slice(0, 60); } console.log(name.padEnd(34), r); };
T('num(Dec neg zero)', () => Value.num({ neg: true, digits: 0n, scale: 0 }));
T('num(Dec negative digits)', () => Value.num({ neg: false, digits: -5n, scale: 0 }));
T('num(Dec scale -1)', () => Value.num({ neg: false, digits: 7n, scale: -1 }));
T('num(Dec digits as string "x")', () => Value.num({ neg: false, digits: 'x', scale: 0 }));
T('num(Dec 1000001 int digits)', () => Value.num({ neg: false, digits: 10n ** 1000001n, scale: 0 }));
