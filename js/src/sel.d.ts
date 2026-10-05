// TypeScript definitions for SEL (Simple Expression Language)
// Public host interface. See spec/SPEC.md §8.
// tools/check-js-dts.mjs holds these to js/src/sel.mjs: every name, member
// and parameter list below exists, and everything public is here.

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

/** SEL's decimal: `digits` × 10^-`scale`, negative when `neg`. */
export interface Decimal {
  neg: boolean;
  digits: bigint;
  scale: number;
}

export class RecordShape {
  readonly keys: readonly string[];
  readonly keyMap: Map<string, number>;
  readonly size: number;
}

export class Value {
  kind: ValueKind;
  scalar: any;
  isList: boolean;
  // A value's children are read through size()/keys()/get()/entries(): how a
  // record or list stores them is internal and changes between releases.

  constructor(kind: ValueKind, scalar: any, isList?: boolean);

  static get NONE(): 'NONE';
  static get TEXT(): 'TEXT';
  static get BIN(): 'BIN';
  static get BOOL(): 'BOOL';

  isNone(): boolean;
  isNull(): boolean;
  isVacuous(): boolean;
  isText(): boolean;
  isBin(): boolean;
  isBool(): boolean;

  // Every constructor checks and copies what it is given (spec §8): a key or
  // text with an unpaired surrogate is E_UTF8, a byte outside 0..255 or a
  // number past the digit caps E_RANGE, anything else it does not take
  // E_BAD_ARG. None of them throws a TypeError.
  static none(): Value;
  static null(): Value;
  static text(s: string): Value;
  static bin(b: Uint8Array | ArrayLike<number>): Value;
  static bool(b: boolean): Value;
  /** A decimal string ("007" becomes "7"), or a decimal in SEL's own form. */
  /** A decimal record must have a boolean `neg`; it is copied. */
  static num(d: string | Decimal): Value;
  static int(n: number | bigint): Value;
  static list(values: Value[]): Value;
  /** A record from keys and values side by side; a repeated key keeps its
   *  first position and takes its last value, as RECORD does. */
  static shaped(keys: readonly string[], values: Value[]): Value;
  /** A record from [key, value] pairs, or with `isList` a list keyed by them
   *  ("1".."n" is a plain list; other keys, which must be distinct, are kept
   *  as FILTER keeps them). */
  static fromEntries(entries: [string, Value][], isList?: boolean): Value;

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
  asDecimal(pos?: Pos | null): Decimal;
  looksNumeric(): boolean;

  clone(pos?: Pos | null): Value;
  cloneAt(depth: number, pos?: Pos | null): Value;

  eql(other: Value, pos?: Pos | null): boolean;
  eqlAt(other: Value, depth: number, pos?: Pos | null): boolean;

  dump(): string;
  dumpAt(depth: number): string;

  /** Plain objects, arrays (holes are NULL), strings, booleans, bigints, whole numbers,
   * Uint8Array, null/undefined. A fraction (a float), a Date, Map, Set, typed array
   * other than Uint8Array or a class instance is E_BAD_ARG (spec §8). */
  static fromNative(x: any): Value;
  static fromNativeAt(x: any, depth: number): Value;

  /** A record whose position-like keys ("0", "2", "10") are not first and ascending has
   * no native form (a JS object would reorder them): E_BAD_ARG. Use entries() for it. */
  toNative(): any;
  toNativeAt(depth: number): any;
}

export class Program {
  readonly source: string;
  /** The parse tree. Its nodes are immutable once constructed: the optimiser
   *  and the SQL planner copy on the way down, and a caller who supplies an
   *  AST is held to the same rule. Reassigning the whole tree is fine and
   *  noticed: the physical tree run() evaluates is keyed by its identity. */
  ast: any;

  constructor(source: string, ast: any);

  run(context?: Value | Record<string, any> | null): Value;
  dependencies(): string[];
  /** The optimised tree run() evaluates, built once per `ast` and cached. */
  physicalAst(): any;
}

export function compile(source: string): Program;
export function evaluate(source: string, context?: Value | Record<string, any> | null): Value;
export function functionNames(): string[];
export function optimizeAst(ast: any): any;
export function optimizeAstLogical(ast: any, options?: Record<string, any>): any;
export function optimizeAstInMemory(ast: any): any;

export interface RegisterOptions {
  lazy?: boolean;
  binds?: boolean;
  arityError?: (count: number) => string | null;
  overwrite?: boolean;
}

export interface BuiltinSpec extends RegisterOptions {
  name: string;
  min: number;
  max?: number;
  fn: (args: any, context: any) => Value;
}

export function register(
  name: string,
  min: number,
  max: number,
  fn: (args: any, context: any) => Value,
  options?: RegisterOptions
): any;
export function register(spec: BuiltinSpec): any;
export const registerBuiltin: typeof register;

/** The argument accessor a host function receives (spec/SPEC.md §8.1). */
export interface HostArgs {
  count(): number;
  val(i: number): Value;
  text(i: number): string;
  bool(i: number): boolean;
  int(i: number): number;
  nonNegInt(i: number): number;
  posOf(i: number): { line: number; col: number; offset: number };
}

/** Adds an application's own strict function; register before compiling a caller. */
export function registerFunction(
  name: string,
  min: number,
  max: number,
  fn: (args: HostArgs) => Value
): void;
