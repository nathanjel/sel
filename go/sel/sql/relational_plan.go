package sql

type bucketState int

const (
	bucketNone bucketState = iota
	bucketOpen
	bucketSealed
)

type relationalProjection struct {
	Alias    *string
	Binder   string
	Node     *sNode
	GroupKey *relationalGroup
}

type relationalFilter struct {
	Binder     string
	Node       *sNode
	Pos        Pos
	OverGroups bool
}

type relationalOrder struct {
	Binder     string
	Node       *sNode
	Dir        string
	Pos        Pos
	OverGroups bool
}

type relationalGroup struct {
	Alias  *string
	Binder string
	Node   *sNode
	Pos    Pos
}

type relationalJoin struct {
	Type           string // "INNER" or "LEFT"
	SourceName     string
	SourceRelation *Binding
	SourceFromRaw  bool
	SourceTable    string
	SourceAlias    string
	LeftNames      []string
	RightNames     []string
	OnPred         *sNode
	Pos            Pos
}

type relationalPlan struct {
	SourceName     string
	RootName       *string
	SourceRelation *Binding
	SourceFromRaw  bool
	SourceTable    string
	SourceAlias    string
	SourceSubquery *relationalPlan
	Joins          []relationalJoin
	Correlate      string
	// OrderLostByJoin: a sort under a join is not the order of the joined rows.
	OrderLostByJoin bool
	// OrderDropped: a derived table with no LIMIT beside its ORDER BY does not keep the
	// order, so a step that needs the rows in that order (a BUCKET's groups, a LINK's
	// rows, a later sort's ties) cannot be built on it.
	OrderDropped bool
	Distinct     bool
	SelectCols   []string
	Projections  []relationalProjection
	Filters      []relationalFilter
	GroupBy      []relationalGroup
	Bucket       bucketState
	BareKey      bool
	Having       []relationalFilter
	OrderBy      []relationalOrder
	Limit        *int64
	Offset       *int64
}

func newRelationalPlan() *relationalPlan {
	return &relationalPlan{
		Bucket: bucketNone,
	}
}
