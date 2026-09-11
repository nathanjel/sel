// Relational Plan IR for statement compilation in JavaScript.

export class RelationalPlan {
  constructor() {
    this.sourceName = '';
    this.sourceRelation = null;
    this.sourceTable = '';
    this.sourceAlias = null;
    this.correlate = null;
    this.distinct = false;
    this.selectCols = null;
    this.projections = null;
    this.filters = [];
    this.groupBy = null;
    this.having = [];
    this.aggregateAliases = {};
    this.orderBy = [];
    this.limit = null;
    this.offset = null;
  }
}
