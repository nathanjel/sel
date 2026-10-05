// TypeScript definitions for SEL -> SQL translation layer
// Public host interface. See docs/sql.md and docs/internals/sql-translation.md §10.
// tools/check-js-dts.mjs holds these to js/src/sql/index.mjs: every name,
// member and parameter list below exists, and everything public is here.

import { Program, Value, Pos } from './sel.js';

export type SqlKind = 'NUM' | 'TEXT' | 'BOOL' | 'BIN' | 'UNKNOWN' | 'LIST' | 'STATEMENT';

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
  /** The closed-vocabulary names of every inexact map entry this translation
   *  used; empty means the translation is exact (see isExact()). */
  caveats: string[];
  /** COLLATION exactness, a different thing from isExact(): this TEXT SQL
   *  already compares bytes the way SEL does -- a column bound with
   *  `exact: true` (a binary collation), or text already put under one -- so a
   *  comparison needs no COLLATE wrap. A builder (map.defineBuilder) may set
   *  it on the fragment it returns. It is composition state: false on what
   *  translate() returns, and it says nothing about caveats. (Named `exact`
   *  before 0.11.) */
  exactCollation: boolean;
  // A number in its canonical form (spec §7.6 CANON).
  canonical: boolean;

  constructor(
    parts: (string | number)[],
    kind: SqlKind,
    dialect: string,
    params?: Value[] | null,
    paramKinds?: SqlKind[] | null,
    caveats?: string[] | null,
    exactCollation?: boolean,
    sargable?: boolean,
    guard?: boolean
  );

  asValue(mode?: RenderMode): string;
  asCondition(mode?: RenderMode): string;
  asStatement(mode?: RenderMode): string;
  bindings(): Value[];
  /** TRANSLATION exactness: true when `caveats` is empty, so the SQL answers
   *  as SEL does for every row. Not the exactCollation flag above. */
  isExact(): boolean;
}

export type ColumnType = 'NUM' | 'TEXT' | 'BOOL' | 'BIN' | 'UNKNOWN';

/** Spellings of a column's collation (docs/sql.md, "Bindings"). */
export type Collation = 'exact' | 'binary' | 'sargable' | 'prefilter' | 'default';
/** Where a relation's pre-filter goes in an EXISTS. */
export type Prefilter = 'separate' | 'inline';

export class Binding {
  readonly spec: any;

  constructor(spec: any);

  /** `exact`: the column already compares bytes exactly; `sargable`: its
   *  collation is case-insensitive; `guard`: a NUM column whose values may not
   *  all be numbers; `collation` spells the first two; `splitSargable` is a
   *  shorthand for `prefilter: 'separate'`. */
  static column(
    column: string,
    table?: string | null,
    type?: ColumnType,
    exact?: boolean,
    sargable?: boolean,
    guard?: boolean,
    collation?: Collation | null,
    prefilter?: Prefilter | null,
    splitSargable?: boolean
  ): Binding;
  static raw(
    sql: string,
    type?: ColumnType,
    exact?: boolean,
    sargable?: boolean,
    guard?: boolean,
    collation?: Collation | null,
    prefilter?: Prefilter | null,
    splitSargable?: boolean
  ): Binding;
  static columns(...items: Binding[]): Binding;
  static relation(
    from: string,
    alias?: string | null,
    fields?: Record<string, Binding> | Map<string, Binding> | null,
    scalar?: string | null,
    correlate?: string | null,
    prefilter?: Prefilter | null,
    splitSargable?: boolean
  ): Binding;
  static relationQuery(
    query: string,
    alias?: string | null,
    fields?: Record<string, Binding> | Map<string, Binding> | null,
    scalar?: string | null,
    correlate?: string | null,
    prefilter?: Prefilter | null,
    splitSargable?: boolean
  ): Binding;
  static value(v: Value, type?: 'NUM' | null): Binding;

  /** On a relation: a single-column unique, non-null key, which enables the
   *  "latest member per group" plan. */
  withUniqueKey(key: string): Binding;
}

export class Bindings {
  constructor(bindings?: Bindings | Record<string, Binding> | Map<string, Binding> | null);

  has(name: string): boolean;
  get(name: string, pos?: Pos | null): any;
  names(): string[];
  checkAliases(pos?: Pos | null): void;
}

// The statement compiler's intermediate representation. No public call returns
// one; the classes are exported for tools that build plans themselves.
export class JoinPlan {
  type: 'INNER' | 'LEFT';
  sourceName: string;
  sourceRelation: any;
  sourceTable: any;
  sourceAlias: string | null;
  /** The names the LINK gives its sides, besides `_1` and `_2` (spec §7.4). */
  leftNames: string[];
  rightNames: string[];
  onPred: any;
  pos: Pos | null;

  constructor();
}

export class RelationalPlan {
  sourceName: string;
  /** The variable the pipeline starts from; null once a LINK has joined. */
  rootName: string | null;
  sourceRelation: any;
  sourceTable: any;
  sourceAlias: string | null;
  sourceSubquery: RelationalPlan | null;
  correlate: string | null;
  joins: JoinPlan[];
  distinct: boolean;
  selectCols: string[] | null;
  projections: any[] | null;
  filters: any[];
  groupBy: any[] | null;
  /** An open or sealed BUCKET, or null. */
  bucket: any;
  /** Whether the grouping was written as a bare BUCKET. */
  bareKey: boolean;
  having: any[];
  orderBy: any[];
  limit: bigint | null;
  /** Set on a derived table built over sorted rows with no LIMIT. */
  orderDropped: boolean;
  offset: bigint | null;

  constructor();
}

export class HybridPlan {
  dialect: string | null;
  sqlStatement: Fragment | null;
  sqlPrefixAst: any;
  continuationAst: any;
  continuationProgram: Program | null;
  continuationSourceVar: string;
  pureSql: boolean;
  pureMemory: boolean;
  sourceTables: string[];
  /** The grouped-latest strategy's keys, or null (docs/internals/sql-translation.md §12.1). */
  selectedMember: { partition_key: string; revision_key: string } | null;
  /** The classification: one of the three words sql/cases uses. */
  readonly kind: 'pure_sql' | 'hybrid' | 'pure_memory';
  readonly isHybrid: boolean;
  readonly is_hybrid: boolean;
  readonly sql_query: Fragment | null;
  readonly sqlQuery: Fragment | null;
  readonly sql_prefix_ast: any;
  readonly continuation_ast: any;
  readonly continuation_program: Program | null;
  readonly continuation_source_var: string;
  readonly pure_sql: boolean;
  readonly pure_memory: boolean;
  readonly source_tables: string[];
  readonly selected_member: { partition_key: string; revision_key: string } | null;
}

export function planHybrid(
  program: Program,
  dialect: string,
  bindings?: Bindings | Record<string, Binding> | Map<string, Binding> | null,
  options?: Record<string, any> | null
): HybridPlan;

export function executeHybrid(
  plan: HybridPlan,
  dbRunner: (sql: string, params: Value[]) => any,
  context?: Value | Record<string, any> | null
): any;

export declare const DIALECTS: Record<string, any>;

/** Runtime extension of the dialect map (sql/MAP.md, docs/extending.md). */
export namespace map {
  export declare const SECTIONS: readonly ['ops', 'funcs', 'skel'];
  /** What entry() answers when no dialect in the chain mentions the key. */
  export declare const MISSING: string;
  /** Every dialect that may be named in a translate() call, sorted. */
  export function targets(): string[];
  /** Whether a dialect (a target or a base) is known. */
  export function exists(dialect: string): boolean;
  /** Define, replace or (with a string or null) withdraw one entry. */
  export function define(dialect: string, section: string, key: string, entry: any): void;
  /** Declare a dialect; sql/MAP.md §3 is the normative list of keys. */
  export function defineDialect(
    name: string,
    spec: {
      extends?: string;
      version?: string;
      target?: boolean;
      lexical?: Record<string, any>;
    }
  ): void;
  /** The escape hatch: an entry built by code from the rendered arguments. */
  export function defineBuilder(
    dialect: string,
    section: string,
    key: string,
    fn: (...args: any[]) => Fragment
  ): void;
  /** Forget every runtime registration (for tests). */
  export function reset(): void;
  /** The dialect, then what it extends, up to ansi. */
  export function chain(dialect: string): readonly string[];
  export function version(dialect: string): string;
  /** A lexical value, or null. */
  export function lexical(dialect: string, key: string): any;
  /** One entry, or MISSING. */
  export function entry(dialect: string, section: string, key: string): any;
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

  static translateStatement(
    program: Program,
    dialect: string,
    bindings?: Bindings | Record<string, Binding> | Map<string, Binding> | null,
    options?: Record<string, any> | null
  ): Fragment;

  static tryTranslateStatement(
    program: Program,
    dialect: string,
    bindings?: Bindings | Record<string, Binding> | Map<string, Binding> | null,
    options?: Record<string, any> | null
  ): Fragment | null;

  static planHybrid(
    program: Program,
    dialect: string,
    bindings?: Bindings | Record<string, Binding> | Map<string, Binding> | null,
    options?: Record<string, any> | null
  ): HybridPlan;

  static executeHybrid(
    plan: HybridPlan,
    dbRunner: (sql: string, params: Value[]) => any,
    context?: Value | Record<string, any> | null
  ): any;

  static dialects(): string[];
}
