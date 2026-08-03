"""Pydantic v2 schemas for the B2B Deal Flow dashboard summary endpoint."""

from __future__ import annotations

from pydantic import BaseModel


class KpiMetricResponse(BaseModel):
    """One top-row KPI card."""

    id: str
    label: str
    value: str
    change_label: str | None = None
    trend: str | None = None


class KanbanDealResponse(BaseModel):
    """One lead rendered as a Kanban card."""

    id: str
    account_name: str
    deal_value_label: str
    score: float | None
    score_reasons: list[str]
    compliance_state: str | None


class KanbanColumnResponse(BaseModel):
    """One Kanban column with its cards."""

    id: str
    title: str
    deals: list[KanbanDealResponse]


class DashboardSummaryResponse(BaseModel):
    """Everything the dashboard's KPI row, Kanban board, and next-action
    banner need in one real-data response."""

    kpis: list[KpiMetricResponse]
    kanban_columns: list[KanbanColumnResponse]
    drafts_awaiting_approval: int
    hot_leads_to_review: int
    replies_to_classify: int
    follow_ups_due: int
