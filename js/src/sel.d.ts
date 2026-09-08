// TypeScript definitions for SEL (Simple Expression Language)
// Public host interface. See spec/SPEC.md §8.

export type ValueKind = 'NONE' | 'TEXT' | 'BIN' | 'BOOL';

export declare const NONE: 'NONE';
export declare const TEXT: 'TEXT';
export declare const BIN: 'BIN';
export declare const BOOL: 'BOOL';

export interface Pos {
  line: number;
  col: number;
  offset: number;
}

export class SelError extends Error {
  readonly code: string;
  readonly line: number;
  readonly col: number;
  readonly offset: number;

  constructor(code: string, message: string, pos?: Pos | null);

  toString(): string;
}

export class Value {
  kind: ValueKind;
  scalar: any;
  children: Map<string, Value> | null;

  constructor(kind: ValueKind, scalar: any);

  static get NONE(): 'NONE';
  static get TEXT(): 'TEXT';
  static get BIN(): 'BIN';
  static get BOOL(): 'BOOL';

  isNone(): boolean;
  isText(): boolean;
  isBin(): boolean;
  isBool(): boolean;

  static none(): Value;
  static text(s: string): Value;
  static bin(b: Uint8Array | ArrayLike<number>): Value;
  static bool(b: boolean): Value;
  static num(d: string): Value;
  static int(n: number | bigint): Value;
  static list(values: Value[]): Value;

  size(): number;
  has(key: string): boolean;
  get(key: string): Value | undefined;
  keys(): string[];
  values(): Value[];
  entries(): [string, Value][];
  set(key: string, value: Value): this;

  scalarSource(pos?: Pos | null): Value;
  asText(pos?: Pos | null): string;
  asBytes(pos?: Pos | null): Uint8Array;
  asBool(pos?: Pos | null): boolean;
  asDecimal(pos?: Pos | null): any;
  looksNumeric(): boolean;

  clone(pos?: Pos | null): Value;
  cloneAt(depth: number, pos?: Pos | null): Value;

  eql(other: Value, pos?: Pos | null): boolean;
  eqlAt(other: Value, depth: number, pos?: Pos | null): boolean;

  dump(): string;
  dumpAt(depth: number): string;

  static fromNative(x: any): Value;
  static fromNativeAt(x: any, depth: number): Value;

  toNative(): any;
  toNativeAt(depth: number): any;
}

export class Program {
  readonly source: string;
  readonly ast: any;

  constructor(source: string, ast: any);

  run(context?: Value | Record<string, any> | null): Value;
  dependencies(): string[];
}

export function compile(source: string): Program;
export function evaluate(source: string, context?: Value | Record<string, any> | null): Value;
export function functionNames(): string[];
