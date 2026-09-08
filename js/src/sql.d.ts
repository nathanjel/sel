// TypeScript definitions for SEL -> SQL translation layer
// Public host interface. See docs/SQL-TRANSLATION.md §10.

import { Program, Value, Pos } from './sel.js';

export type SqlKind = 'NUM' | 'TEXT' | 'BOOL' | 'BIN' | 'UNKNOWN' | 'LIST';
export declare const KINDS: readonly SqlKind[];

export type RenderMode = 'inline' | 'params' | 'debug';

export class SqlError extends Error {
  readonly code: string;
  readonly line: number;
  readonly col: number;
  readonly offset: number;

  constructor(code: string, message: string, pos?: Pos | null);

  toString(): string;
}

export class Fragment {
  parts: (string | number)[];
  kind: SqlKind;
  dialect: string;
  params: Value[];
  paramKinds: SqlKind[];
  caveats: string[];

  constructor(
    parts: (string | number)[],
    kind: SqlKind,
    dialect: string,
    params?: Value[] | null,
    paramKinds?: SqlKind[] | null,
    caveats?: string[] | null
  );

  asValue(mode?: RenderMode): string;
  asCondition(mode?: RenderMode): string;
  bindings(): Value[];
}

export type ColumnType = 'NUM' | 'TEXT' | 'BOOL' | 'BIN' | 'UNKNOWN';

export class Binding {
  readonly spec: any;

  constructor(spec: any);

  static column(column: string, table?: string | null, type?: ColumnType): Binding;
  static raw(sql: string, type?: ColumnType): Binding;
  static columns(...items: Binding[]): Binding;
  static relation(
    from: string,
    alias?: string | null,
    fields?: Record<string, Binding> | Map<string, Binding> | null,
    scalar?: string | null,
    correlate?: string | null
  ): Binding;
  static relationQuery(
    query: string,
    alias?: string | null,
    fields?: Record<string, Binding> | Map<string, Binding> | null,
    scalar?: string | null,
    correlate?: string | null
  ): Binding;
  static value(v: Value, type?: 'NUM' | null): Binding;
}

export class Bindings {
  constructor(bindings?: Bindings | Record<string, Binding> | Map<string, Binding> | null);

  has(name: string): boolean;
  get(name: string, pos?: Pos | null): any;
  names(): string[];
  checkAliases(pos?: Pos | null): void;
}

export declare const DIALECTS: Record<string, any>;

export namespace map {
  export declare const SECTIONS: readonly ['ops', 'funcs', 'skel'];
  export declare const MISSING: string;
  export function targets(): string[];
  export function hasDialect(name: string): boolean;
  export function define(dialect: string, section: string, key: string, entry: any): void;
  export function defineDialect(
    name: string,
    parent?: string | null,
    options?: {
      comment?: string;
      quote?: string;
      textCollate?: string;
      numericGuard?: string;
    } | null
  ): void;
  export function lookup(dialect: string, section: string, key: string): any;
}

export class Sql {
  static translate(
    program: Program,
    dialect: string,
    bindings?: Bindings | Record<string, Binding> | Map<string, Binding> | null,
    options?: Record<string, any> | null
  ): Fragment;

  static tryTranslate(
    program: Program,
    dialect: string,
    bindings?: Bindings | Record<string, Binding> | Map<string, Binding> | null,
    options?: Record<string, any> | null
  ): Fragment | null;

  static dialects(): string[];
}
