/**
 * Shared shapes for the B2B Deal Flow dashboard.
 *
 * This phase renders mock data only (see src/lib/mockDashboardData.ts). Once
 * a later phase wires this page to the real backend, these types should be
 * kept in sync with the corresponding Pydantic response schemas under
 * backend/app/schemas/ (per AGENTS.md section 4.1) -- e.g. KpiMetric maps
 * loosely onto pipeline/analytics summary fields, DealCard onto LeadResponse,
 * FollowUpRow onto OutreachDraft + inbound-message history.
 */

/** Direction of a KPI's period-over-period change. */
export type KpiTrend = "up" | "down" | "flat";

/** One top-row summary metric card (e.g. "Total Pipeline"). */
export interface KpiMetric {
  id: string;
  label: string;
  value: string;
  changeLabel?: string;
  trend?: KpiTrend;
}

/** One deal card inside a Kanban funnel column. */
export interface DealCard {
  id: string;
  accountName: string;
  dealValueLabel: string;
  /** Raw 0–1 lead score; rendered as a Hot/Warm/Cold label, never a number. */
  score?: number;
  /** Rep-friendly reason fragments behind the score, e.g. ["old website"]. */
  scoreReasons?: readonly string[];
  /** Compliance block, if any — rendered as a plain sentence, not a raw flag. */
  complianceState?:
    | "unsubscribed"
    | "hard_bounce"
    | "spam_complaint"
    | "manual"
    | "do_not_contact"
    | "gdpr_deleted";
}

/** One stage/column of the Kanban deal funnel. */
export interface FunnelColumn {
  id: string;
  title: string;
  deals: DealCard[];
}

/** One bar in the monthly partner-outreach volume chart. */
export interface OutreachVolumePoint {
  month: string;
  emails: number;
  calls: number;
}

/** One slice of the response-rate donut chart. */
export interface ResponseRateSlice {
  label: string;
  value: number;
  colorClassName: string;
}

/** One ranked entry in the "Top Partner Locations" panel. */
export interface PartnerLocation {
  id: string;
  place: string;
  partnerCount: number;
  shareOfTotalPct: number;
}

/** Delivery/engagement status of one automated follow-up. */
export type FollowUpStatus = "Sent" | "Opened" | "Pending";

/** One row of the Automated Follow-up Tracker table. */
export interface FollowUpRow {
  id: string;
  contactName: string;
  lastInteractionLabel: string;
  status: FollowUpStatus;
  aiSuggestion: string;
  reviewed: boolean;
}

/** Category of a node in the workflow-automation library palette. */
export type WorkflowNodeKind = "trigger" | "action" | "condition";

/** One entry in the workflow builder's node library sidebar. */
export interface WorkflowLibraryItem {
  id: string;
  label: string;
  kind: WorkflowNodeKind;
}

/** One placed node on the workflow automation canvas. */
export interface WorkflowCanvasNode {
  id: string;
  kind: WorkflowNodeKind;
  title: string;
  subtitle: string;
  /** Percent-based position within the canvas, so the layout stays responsive. */
  x: number;
  y: number;
}

/** One connector line between two canvas nodes, by node id. */
export interface WorkflowCanvasEdge {
  id: string;
  fromNodeId: string;
  toNodeId: string;
}

/** One entry in the "Recent workflows" list. */
export interface RecentWorkflow {
  id: string;
  label: string;
}
