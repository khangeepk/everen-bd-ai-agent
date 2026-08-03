/**
 * Real approval-queue data + actions.
 *
 * Fetches GET /outreach/queue/enriched (draft + lead + audit findings +
 * recommended service, joined server-side -- see
 * backend/app/api/v1/outreach.py::list_queue_enriched) and GET
 * /outreach/quota, and maps them onto the same ApprovalDraft/SendQuota
 * shapes the approval-review screen already renders. Falls back to mock
 * data only when no dev session is available (same real/mock split as
 * chatQueries.ts and outreachQueueApi.ts); a real call that fails surfaces
 * an explicit error instead of silently swapping in samples.
 */

import { ApiError, apiFetch, hasApiToken } from "@/lib/apiClient";
import { mockApprovalDrafts, mockSendQuota } from "@/lib/mockApprovals";
import type { AuditCategory, ComplianceState } from "@/lib/plainLanguage";
import type { ApprovalDraft, SendQuota } from "@/types/approval";

/** Backend FindingCategory values the frontend's AuditCategory union renders.
 * A finding in a category outside this set (e.g. accessibility,
 * best_practices, broken_links) is dropped rather than mis-rendered --
 * see toAuditCategory(). */
const KNOWN_AUDIT_CATEGORIES: readonly string[] = [
  "performance",
  "seo",
  "security",
  "mobile",
  "contact_form",
  "social",
];

/** Channels the approval-review screen knows how to render. call_script
 * drafts are excluded here -- they're a document for a human to read on a
 * call, not a review-queue item with a channel badge. */
const REVIEWABLE_CHANNELS: readonly string[] = ["email", "linkedin", "whatsapp"];

function toAuditCategory(category: string): AuditCategory | null {
  return (KNOWN_AUDIT_CATEGORIES as readonly string[]).includes(category)
    ? (category as AuditCategory)
    : null;
}

interface DetectedProblemApiResult {
  category: string;
  title: string;
  detail: string | null;
}

interface DraftClaimApiResult {
  phrase: string;
  source: string;
  evidence: string | null;
}

interface EnrichedLeadApiResult {
  id: string;
  name: string;
  industry: string | null;
  location: string | null;
}

interface EnrichedDraftApiResult {
  id: string;
  lead: EnrichedLeadApiResult;
  channel: string;
  status: string;
  subject: string | null;
  body: string;
  score: number | null;
  score_reasons: string[];
  compliance_state: string | null;
  problems: DetectedProblemApiResult[];
  claims: DraftClaimApiResult[];
  recommended_service: string | null;
}

interface PaginatedEnrichedDraftsApiResponse {
  items: EnrichedDraftApiResult[];
  total: number;
  page: number;
  page_size: number;
}

interface QuotaStatusApiResponse {
  channel: string;
  quota_date: string;
  limit: number;
  used: number;
  remaining: number;
  resets_at: string;
}

/** How many pending drafts to review in one sitting. Same page-size
 * convention as the LinkedIn queue (LINKEDIN_QUEUE_PAGE_SIZE). */
export const APPROVAL_QUEUE_PAGE_SIZE = 50;

export interface ApprovalQueueOutcome {
  drafts: ApprovalDraft[];
  quota: SendQuota;
  isMock: boolean;
  /** Set when a real (token-available) call failed -- never a silent
   * sample-data swap, matching every other real data source in this app. */
  error?: string;
}

function toApprovalDraft(row: EnrichedDraftApiResult): ApprovalDraft {
  const channel = REVIEWABLE_CHANNELS.includes(row.channel)
    ? (row.channel as ApprovalDraft["channel"])
    : "email";

  return {
    id: row.id,
    leadName: row.lead.name,
    industry: row.lead.industry ?? "Unknown industry",
    location: row.lead.location ?? "Unknown location",
    // Backend sends null for a lead that's never been scored yet -- 0 here
    // renders as "Cold" / "No strong signals yet" via scoreToLabel(), an
    // honest default rather than hiding the card.
    score: row.score ?? 0,
    scoreReasons: row.score_reasons,
    channel,
    subject: row.subject ?? undefined,
    body: row.body,
    problems: row.problems
      .map((p) => {
        const category = toAuditCategory(p.category);
        return category ? { category, detail: p.detail ?? undefined } : null;
      })
      .filter((p): p is NonNullable<typeof p> => p !== null),
    recommendedService: row.recommended_service ?? "Not yet matched to a service",
    claims: row.claims
      .map((c) => {
        const source = toAuditCategory(c.source);
        return source ? { phrase: c.phrase, source, evidence: c.evidence ?? undefined } : null;
      })
      .filter((c): c is NonNullable<typeof c> => c !== null),
    complianceState: (row.compliance_state ?? undefined) as ComplianceState | undefined,
  };
}

/**
 * Fetch the real approval queue (pending-review drafts) and today's send
 * quota.
 *
 * @returns Drafts + quota, and whether they're real or mock.
 */
export async function fetchApprovalQueue(): Promise<ApprovalQueueOutcome> {
  if (!(await hasApiToken())) {
    return { drafts: mockApprovalDrafts, quota: mockSendQuota, isMock: true };
  }

  try {
    const [page, quotaResponse] = await Promise.all([
      apiFetch<PaginatedEnrichedDraftsApiResponse>("/outreach/queue/enriched", {
        searchParams: { status: "pending_review", page: 1, page_size: APPROVAL_QUEUE_PAGE_SIZE },
      }),
      apiFetch<QuotaStatusApiResponse>("/outreach/quota", { searchParams: { channel: "email" } }),
    ]);

    const drafts = page.items
      .filter((row) => REVIEWABLE_CHANNELS.includes(row.channel))
      .map(toApprovalDraft);
    const quota: SendQuota = { dailyLimit: quotaResponse.limit, sentToday: quotaResponse.used };

    return { drafts, quota, isMock: false };
  } catch (error) {
    return {
      drafts: [],
      quota: mockSendQuota,
      isMock: false,
      error: describeError(error),
    };
  }
}

/**
 * Approve a draft for sending (does NOT send -- see AGENTS.md section 8).
 *
 * @param draftId - The draft to approve.
 * @throws ApiError if the approval is refused (e.g. no longer pending, or
 *   the lead became ineligible since the draft was generated).
 */
export async function approveDraft(draftId: string): Promise<void> {
  await apiFetch(`/outreach/drafts/${draftId}/approve`, { method: "POST", body: {} });
}

/**
 * Reject a draft with a reason.
 *
 * @param draftId - The draft to reject.
 * @param reason - Why it was rejected (required by the backend, 1-500 chars).
 */
export async function rejectDraft(draftId: string, reason: string): Promise<void> {
  await apiFetch(`/outreach/drafts/${draftId}/reject`, {
    method: "POST",
    body: { reason },
  });
}

function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    return error.status ? `API returned ${error.status}: ${error.message}` : error.message;
  }
  return "Unexpected error calling the API.";
}
