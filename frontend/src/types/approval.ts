import type { AuditCategory, ComplianceState } from "@/lib/plainLanguage";

/** A personalized claim in the draft, tied back to the finding that produced it. */
export interface DraftClaim {
  /** The exact phrase in the draft body (used to highlight + explain it). */
  phrase: string;
  /** The audit finding category this claim is based on. */
  source: AuditCategory;
  /** Optional raw metric behind the finding, e.g. "8.2s load time". */
  evidence?: string;
}

/** One detected problem for the lead, shown in the context column. */
export interface DetectedProblem {
  category: AuditCategory;
  /** Optional short raw detail, e.g. "loads in 8s". */
  detail?: string;
}

/** A single draft awaiting human approval, with everything needed to decide. */
export interface ApprovalDraft {
  id: string;
  leadName: string;
  industry: string;
  location: string;
  /** Raw 0–1 lead score (rendered as a label, never a number). */
  score: number;
  scoreReasons: readonly string[];
  /** Channel the draft will go out on. */
  channel: "email" | "linkedin" | "whatsapp";
  /** Email subject, when channel is email. */
  subject?: string;
  /** The draft message body. */
  body: string;
  /** Problems the audit found, shown as business consequences. */
  problems: readonly DetectedProblem[];
  /** The service the agent recommends pitching, in plain words. */
  recommendedService: string;
  /** Provenance: each personalized claim linked to its source finding. */
  claims: readonly DraftClaim[];
  /** Set when the lead is blocked — approval is disabled and reason shown. */
  complianceState?: ComplianceState;
}

/** The daily send quota context, surfaced on the review screen itself. */
export interface SendQuota {
  dailyLimit: number;
  sentToday: number;
}

/** A decision the reviewer makes on a draft. */
export type ReviewDecision = "approve" | "reject" | "skip";
