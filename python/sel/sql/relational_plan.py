"""Relational Plan IR for statement compilation in Python."""

from __future__ import annotations

from typing import Any


class JoinPlan:
    def __init__(self) -> None:
        self.type: str = 'INNER'
        self.source_name: str = ''
        self.source_relation: dict[str, Any] | None = None
        self.source_table: Any = ''
        self.source_alias: str | None = None
        self.left_binder: str = '_1'
        self.right_binder: str = '_2'
        self.on_pred: Any = None
        self.pos: Any = None


class RelationalPlan:
    def __init__(self) -> None:
        self.source_name: str = ''
        self.source_relation: dict[str, Any] | None = None
        self.source_table: Any = ''
        self.source_alias: str | None = None
        self.source_subquery: RelationalPlan | None = None
        self.joins: list[JoinPlan] = []
        self.correlate: str | None = None
        self.distinct: bool = False
        self.select_cols: list[str] | None = None
        self.projections: list[dict[str, Any]] | None = None
        self.filters: list[dict[str, Any]] = []
        self.group_by: list[dict[str, Any]] | None = None
        # A BUCKET without a projection leaves the plan 'open': its SQL rows
        # are the group keys, which is not what SEL's buckets are (a map of
        # member rows), so the next MAP is folded into the bucket as its
        # projection -- the one SQL shape a bucket has. Any other step first
        # turns it 'sealed': the members are gone for good, and a MAP after
        # that is refused rather than evaluated over rows SEL would have
        # called groups.
        self.bucket: str | None = None
        # Whether the grouping was written as a bare BUCKET (with or without
        # the MAP that closes it). A bare bucket's key is an index key: SEL
        # refuses a boolean, binary, list or record key, so the translator
        # must too.
        self.bare_key: bool = False
        self.having: list[dict[str, Any]] = []
        self.order_by: list[dict[str, Any]] = []
        self.limit: int | None = None
        self.offset: int | None = None
