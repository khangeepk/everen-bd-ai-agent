/**
 * Shared shapes for the LinkedIn outreach queue page.
 *
 * Like src/types/chat.ts, the API* types below are a minimal subset of the
 * real Pydantic response schemas this feature actually reads (backend/app/
 * schemas/outreach.py's DraftResponse and PaginatedDrafts, backend/app/
 * schemas/lead.py's LeadResponse) -- not the full response shape.
 */

/** Subset of backend DraftResponse, scoped to what a LinkedIn draft card needs. */
export interface LinkedInDraftApiResult {
  id: string;
  lead_id: string;
  status: string;
  /** The connection-request note -- LinkedIn's own 300-character limit
   * applies, already enforced server-side (backend/app/agents/outreach.py). */
  body: string;
  /** The follow-up message to send after the prospect accepts. Null only if
   * generation somehow produced neither piece -- not expected in practice,
   * since the backend always falls back to deterministic text. */
  linkedin_followup_message: string | null;
  review_warnings: string | null;
  used_fallback: boolean;
  created_at: string;
}

/** Subset of backend PaginatedDrafts. */
export interface LinkedInQueueApiResponse {
  items: LinkedInDraftApiResult[];
  total: number;
}

/** Subset of backend LeadResponse -- just enough to label a draft card. */
export interface LeadSummaryApiResult {
  id: string;
  name: string;
  contact_name: string | null;
  linkedin_url: string | null;
}

/** One LinkedIn draft, joined with its lead's display info, ready to render. */
export interface LinkedInQueueItem {
  draftId: string;
  leadId: string;
  leadName: string;
  contactName: string | null;
  linkedinUrl: string | null;
  status: string;
  connectionNote: string;
  followUpMessage: string | null;
  warnings: string[];
  usedFallback: boolean;
  createdAt: string;
}
