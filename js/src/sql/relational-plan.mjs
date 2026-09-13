// Relational Plan IR for statement compilation in JavaScript.

export class JoinPlan {
  constructor() {
    this.type = 'INNER';
    this.sourceName = '';
    this.sourceRelation = null;
    this.sourceTable = '';
    this.sourceAlias = null;
    this.leftBinder = '_1';
    this.rightBinder = '_2';
    this.onPred = null;
    this.pos = null;
  }

  get kind() { return this.type; }
  set kind(value) { this.type = value; }
}

export class RelationalPlan {
  constructor() {
    this.sourceName = '';
    this.sourceRelation = null;
    this.sourceTable = '';
    this.sourceAlias = null;
    this.sourceSubquery = null;
    this.correlate = null;
    this.joins = [];
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
