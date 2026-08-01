/**
 * Shared shapes for the per-lead pipeline workflow grid (spreadsheet view).
 *
 * This phase renders mock data only (see src/lib/mockWorkflowData.ts),
 * matching the pattern already set by src/types/dashboard.ts -- confirmed
 * with the user before wiring to the real backend. Once that later phase
 * happens, PipelineStepKey should map onto what already exists server-side:
 * "discovered" -> Lead itself (source/created_at), "enriched" -> EmailSource
 * (app/services/email_enrichment.py), "audited" -> WebsiteAudit,
 * "scored" -> LeadScore, "drafted"/"approved"/"sent" -> OutreachDraft.status,
 * "replied" -> InboundMessage.
 */

/** One column of the workflow grid, in pipeline order. Fixed for now -- no
 * drag-drop column builder yet, per this phase's scope. */
export type PipelineStepKey =
  | "discovered"
  | "enriched"
  | "audited"
  | "scored"
  | "drafted"
  | "approved"
  | "sent"
  | "replied";

/** Status of one lead's progress through one pipeline step. */
export type StepStatus = "done" | "in_progress" | "pending" | "failed" | "not_started";

/**
 * What clicking a cell in this column does.
 *
 * - "rerun": re-executes that step for that lead (the enrich/audit/score/draft
 *   agents all have a real single-lead endpoint already -- see
 *   backend/app/api/v1/email_enrichment.py, audits.py, lead_scores.py,
 *   outreach.py).
 * - "review": opens the underlying draft for full human review rather than
 *   firing an action directly. "approved" and "sent" are gated by AGENTS.md
 *   section 8's non-negotiable human-approval rule, so a grid cell must
 *   never one-click approve or send -- it only ever opens the draft.
 * - "detail": read-only. "discovered" is an origin event (nothing to
 *   re-run); "replied" is inbound-only (nothing this system generated).
 */
export type StepInteraction = "rerun" | "review" | "detail";

/** Static metadata describing one column, independent of any lead's data. */
export interface PipelineStepMeta {
  key: PipelineStepKey;
  label: string;
  interaction: StepInteraction;
}

/** One lead's status for a single pipeline step. */
export interface WorkflowStepCell {
  status: StepStatus;
  /** Short human-readable detail, e.g. "3 findings" or "Objection: price". */
  detail?: string;
  /** When this step last ran/changed, already formatted for display. */
  timestampLabel?: string;
}

/** One row of the workflow grid: one lead, its steps keyed by PipelineStepKey. */
export interface WorkflowLeadRow {
  id: string;
  leadName: string;
  category: string;
  steps: Record<PipelineStepKey, WorkflowStepCell>;
}
