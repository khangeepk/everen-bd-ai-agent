/**
 * Fetches the real LinkedIn outreach queue when possible, falls back to
 * mock data (src/lib/mockOutreachQueue.ts) when no API token is configured
 * or the real call fails -- same real/mock split as src/lib/chatQueries.ts.
 *
 * No new backend logic: this calls the existing GET /outreach/queue
 * (filtered to channel=linkedin, status=pending_review) and, for each
 * distinct lead on the page, GET /leads/{id} to get a display name -- the
 * same N+1-but-parallel pattern chatQueries.ts already uses for
 * score-filtered lead queries.
 */

import { ApiError, apiFetch, hasApiToken } from "@/lib/apiClient";
import { mockLinkedInQueue } from "@/lib/mockOutreachQueue";
import type {
  LeadSummaryApiResult,
  LinkedInDraftApiResult,
  LinkedInQueueApiResponse,
  LinkedInQueueItem,
} from "@/types/outreachQueue";

/** How many pending LinkedIn drafts to fetch at once. GET /outreach/queue
 * caps page_size at 100 (same convention as GET /leads); 50 is plenty for a
 * queue a rep works through in one sitting. */
export const LINKEDIN_QUEUE_PAGE_SIZE = 50;

export interface LinkedInQueueOutcome {
  items: LinkedInQueueItem[];
  isMock: boolean;
  /** Set when a real (token-available) call failed. Callers must show an
   * error + retry for this case -- NOT fall back to sample data, so a real
   * outage is never mistaken for "the queue is just empty" or masked behind
   * cheerful sample drafts. Mirrors src/lib/chatQueries.ts's ChatQueryOutcome. */
  error?: string;
}

/**
 * Fetch pending-review LinkedIn drafts, joined with each lead's display info.
 *
 * @returns The queue items and whether they're real or mock.
 */
export async function fetchLinkedInQueue(): Promise<LinkedInQueueOutcome> {
  if (!(await hasApiToken())) {
    return { items: mockLinkedInQueue, isMock: true };
  }

  try {
    const page = await apiFetch<LinkedInQueueApiResponse>("/outreach/queue", {
      searchParams: {
        channel: "linkedin",
        status: "pending_review",
        page: 1,
        page_size: LINKEDIN_QUEUE_PAGE_SIZE,
      },
    });

    const leadIds = Array.from(new Set(page.items.map((draft) => draft.lead_id)));
    const leadsById = new Map<string, LeadSummaryApiResult>();
    await Promise.all(
      leadIds.map(async (leadId) => {
        try {
          const lead = await apiFetch<LeadSummaryApiResult>(`/leads/${leadId}`);
          leadsById.set(leadId, lead);
        } catch {
          // A lead that 404s or fails to load doesn't sink the whole queue --
          // the draft still renders, just with a generic fallback label
          // (see toQueueItem below) instead of the lead's real name.
        }
      }),
    );

    const items = page.items.map((draft) => toQueueItem(draft, leadsById.get(draft.lead_id)));
    return { items, isMock: false };
  } catch (error) {
    // Token/session was available, so this was a real attempt that failed --
    // surface it for an explicit retry, never silently swap in sample drafts.
    return { items: [], isMock: false, error: describeError(error) };
  }
}

function toQueueItem(
  draft: LinkedInDraftApiResult,
  lead: LeadSummaryApiResult | undefined,
): LinkedInQueueItem {
  return {
    draftId: draft.id,
    leadId: draft.lead_id,
    leadName: lead?.name ?? "(lead details unavailable)",
    contactName: lead?.contact_name ?? null,
    linkedinUrl: lead?.linkedin_url ?? null,
    status: draft.status,
    connectionNote: draft.body,
    followUpMessage: draft.linkedin_followup_message,
    warnings: draft.review_warnings ? draft.review_warnings.split("\n") : [],
    usedFallback: draft.used_fallback,
    createdAt: draft.created_at,
  };
}

function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    return error.status ? `API returned ${error.status}: ${error.message}` : error.message;
  }
  return "Unexpected error calling the API.";
}
