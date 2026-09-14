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
    // A BUCKET without a projection leaves the plan 'open': its SQL rows are
    // the group keys, which is not what SEL's buckets are (a map of member
    // rows), so the next MAP is folded into the bucket as its projection --
    // that is the one SQL shape a bucket has. Any other step first turns it
    // 'sealed': the members are gone for good, and a MAP after that is refused
    // rather than evaluated over rows SEL would have called groups.
    this.bucket = null;
    this.having = [];
    this.aggregateAliases = {};
    this.orderBy = [];
    this.limit = null;
    this.offset = null;
  }
}
