package sql

type BucketState int

const (
	BucketNone BucketState = iota
	BucketOpen
	BucketSealed
)

type RelationalProjection struct {
	Alias    *string
	Binder   string
	Node     *SNode
	GroupKey *RelationalGroup
}

type RelationalFilter struct {
	Binder     string
	Node       *SNode
	Pos        Pos
	OverGroups bool
}

type RelationalOrder struct {
	Binder     string
	Node       *SNode
	Dir        string
	Pos        Pos
	OverGroups bool
}

type RelationalGroup struct {
	Alias  *string
	Binder string
	Node   *SNode
	Pos    Pos
}

type RelationalJoin struct {
	Type           string // "INNER" or "LEFT"
	SourceName     string
	SourceRelation *Binding
	SourceFromRaw  bool
	SourceTable    string
	SourceAlias    string
	LeftNames      []string
	RightNames     []string
	OnPred         *SNode
	Pos            Pos
}

type RelationalPlan struct {
	SourceName     string
	RootName       *string
	SourceRelation *Binding
	SourceFromRaw  bool
	SourceTable    string
	SourceAlias    string
	SourceSubquery *RelationalPlan
	Joins          []RelationalJoin
	Correlate      string
	Distinct       bool
	SelectCols     []string
	Projections    []RelationalProjection
	Filters        []RelationalFilter
	GroupBy        []RelationalGroup
	Bucket         BucketState
	BareKey        bool
	Having         []RelationalFilter
	OrderBy        []RelationalOrder
	Limit          *int64
	Offset         *int64
}

func NewRelationalPlan() *RelationalPlan {
	return &RelationalPlan{
		Bucket: BucketNone,
	}
}
