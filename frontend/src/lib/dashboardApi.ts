/**
 * Real dashboard summary data: KPI cards, Kanban columns, and the counts
 * behind the "what should I do now?" banner.
 *
 * Calls GET /analytics/dashboard-summary (see
 * backend/app/services/dashboard_summary.py) -- one real, server-side
 * aggregated call, no frontend N+1. Falls back to the existing mock dataset
 * only when no dev session is available; a real call that fails surfaces an
 * explicit error instead of silently swapping in samples, same as every
 * other real data source in this app.
 */

import { apiFetch, ApiError, hasApiToken } from "@/lib/apiClient";
import { funnelColumns as mockFunnelColumns, kpiMetrics as mockKpiMetrics } from "@/lib/mockDashboardData";
import type { DealCard, FunnelColumn, KpiMetric } from "@/types/dashboard";
import type { PendingWorkCounts } from "@/types/onboarding";

interface KpiMetricApiResult {
  id: string;
  label: string;
  value: string;
  change_label: string | null;
  trend: string | null;
}

interface KanbanDealApiResult {
  id: string;
  account_name: string;
  deal_value_label: string;
  score: number | null;
  score_reasons: string[];
  compliance_state: string | null;
}

interface KanbanColumnApiResult {
  id: string;
  title: string;
  deals: KanbanDealApiResult[];
}

interface DashboardSummaryApiResponse {
  kpis: KpiMetricApiResult[];
  kanban_columns: KanbanColumnApiResult[];
  drafts_awaiting_approval: number;
  hot_leads_to_review: number;
  replies_to_classify: number;
  follow_ups_due: number;
}

export interface DashboardSummaryOutcome {
  kpis: KpiMetric[];
  kanbanColumns: FunnelColumn[];
  pendingWork: PendingWorkCounts;
  isMock: boolean;
  /** Set when a real (token-available) call failed. */
  error?: string;
}

const MOCK_PENDING_WORK: PendingWorkCounts = {
  draftsAwaitingApproval: 12,
  hotLeadsToReview: mockFunnelColumns[0]?.deals.length ?? 0,
  repliesToClassify: 0,
  followUpsDue: 3,
};

function toDealCard(row: KanbanDealApiResult): DealCard {
  return {
    id: row.id,
    accountName: row.account_name,
    dealValueLabel: row.deal_value_label,
    score: row.score ?? undefined,
    scoreReasons: row.score_reasons,
    complianceState: (row.compliance_state ?? undefined) as DealCard["complianceState"],
  };
}

function toKpiMetric(row: KpiMetricApiResult): KpiMetric {
  return {
    id: row.id,
    label: row.label,
    value: row.value,
    changeLabel: row.change_label ?? undefined,
    trend: (row.trend ?? undefined) as KpiMetric["trend"],
  };
}

/**
 * Fetch the real dashboard summary.
 *
 * @returns KPI cards, Kanban columns, and next-action counts, real or mock.
 */
export async function fetchDashboardSummary(): Promise<DashboardSummaryOutcome> {
  if (!(await hasApiToken())) {
    return {
      kpis: mockKpiMetrics,
      kanbanColumns: mockFunnelColumns,
      pendingWork: MOCK_PENDING_WORK,
      isMock: true,
    };
  }

  try {
    const summary = await apiFetch<DashboardSummaryApiResponse>("/analytics/dashboard-summary");
    return {
      kpis: summary.kpis.map(toKpiMetric),
      kanbanColumns: summary.kanban_columns.map((col) => ({
        id: col.id,
        title: col.title,
        deals: col.deals.map(toDealCard),
      })),
      pendingWork: {
        draftsAwaitingApproval: summary.drafts_awaiting_approval,
        hotLeadsToReview: summary.hot_leads_to_review,
        repliesToClassify: summary.replies_to_classify,
        followUpsDue: summary.follow_ups_due,
      },
      isMock: false,
    };
  } catch (error) {
    return {
      kpis: [],
      kanbanColumns: [],
      pendingWork: { draftsAwaitingApproval: 0, hotLeadsToReview: 0, repliesToClassify: 0, followUpsDue: 0 },
      isMock: false,
      error: describeError(error),
    };
  }
}

function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    return error.status ? `API returned ${error.status}: ${error.message}` : error.message;
  }
  return "Unexpected error calling the API.";
}
