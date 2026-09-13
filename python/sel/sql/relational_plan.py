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
        self.having: list[dict[str, Any]] = []
        self.aggregate_aliases: dict[str, Any] = {}
        self.order_by: list[dict[str, Any]] = []
        self.limit: int | None = None
        self.offset: int | None = None
