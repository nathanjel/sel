// JS-P27: fromCodePoints sliced even short arrays.
import { bench, root } from './lib.mjs';
import { pathToFileURL } from 'node:url';
import { resolve, dirname } from 'node:path';
const base = process.env.SEL_JS_ENTRY ? dirname(process.env.SEL_JS_ENTRY) : resolve(root, 'js/src');
const { fromCodePoints } = await import(pathToFileURL(resolve(base, 'utf8.mjs')).href);
const ten = [72, 101, 108, 108, 111, 0x142, 0x1f600, 33, 97, 98], one = [0x1f600], empty = [], big = Array.from({ length: 1000000 }, (_, i) => 97 + (i % 26));
const R = 1000000;
bench(`P27 fromCodePoints(10 elements) x${R}`, () => { let n = 0; for (let i = 0; i < R; i++) n += fromCodePoints(ten).length; return n; }, { reps: 5 });
bench(`P27 fromCodePoints(1 element) x${R}`, () => { let n = 0; for (let i = 0; i < R; i++) n += fromCodePoints(one).length; return n; }, { reps: 5 });
bench(`P27 fromCodePoints([]) x${R}`, () => { let n = 0; for (let i = 0; i < R; i++) n += fromCodePoints(empty).length; return n; }, { reps: 5 });
bench('P27 fromCodePoints(1M elements) x5', () => { let n = 0; for (let i = 0; i < 5; i++) n += fromCodePoints(big).length; return n; }, { reps: 5 });
