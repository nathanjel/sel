// JS-P28 micro: structuralHash of a packed list of 8 numbers and of a 32-byte BIN.
import { bench, root } from './lib.mjs';
import { pathToFileURL } from 'node:url';
import { resolve, dirname } from 'node:path';
const base = process.env.SEL_JS_ENTRY ? dirname(process.env.SEL_JS_ENTRY) : resolve(root, 'js/src');
const { Value, structuralHash } = await import(pathToFileURL(resolve(base, 'value.mjs')).href);
const list = Value.fromNative([1, 2, 3, 0, 1, 2, 3, 1]);
const bin = Value.bin(Uint8Array.from({ length: 32 }, (_, i) => i * 7 % 256));
const R = 1000000;
bench(`P28 structuralHash(list of 8) x${R}`, () => { let n = 0; for (let i = 0; i < R; i++) n ^= structuralHash(list); return n; }, { reps: 5 });
bench(`P28 structuralHash(BIN 32 bytes) x${R}`, () => { let n = 0; for (let i = 0; i < R; i++) n ^= structuralHash(bin); return n; }, { reps: 5 });
