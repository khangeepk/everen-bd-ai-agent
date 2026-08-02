/**
 * Orchestrates a chat query: calls the real backend when possible, falls
 * back to mock results (src/lib/mockChatResults.ts) when no API token is
 * configured or the real call fails.
 *
 * No new backend logic -- these are exactly the existing endpoints:
 * GET /leads, GET /leads/{id}/score (only when a query needs score
 * filtering), and POST /places/search.
 */

import { ApiError, apiFetch, hasApiToken } from "@/lib/apiClient";
import { mockLeadsResults, mockPlacesResults } from "@/lib/mockChatResults";
import type {
  ChatResults,
  LeadApiResult,
  LeadResultRow,
  LeadScoreApiResult,
  LeadsApiResponse,
  ParsedIntent,
  PlaceApiResult,
  PlaceResultRow,
  PlaceSearchApiResponse,
} from "@/types/chat";

/** How many leads to fetch per query. GET /leads caps page_size at 100
 * (AGENTS.md section 9.3); 50 keeps a score-filtered query's N+1 lookups
 * (see runLeadsQuery) to a reasonable number of requests. Exported so the
 * chat panel can say exactly what it checked in a score-filtered response. */
export const LEADS_PAGE_SIZE = 50;

export interface ChatQueryOutcome {
  results: ChatResults;
  isMock: boolean;
  /** Set when a token IS configured but the real call failed. Callers must
   * show an error + retry for this case -- NOT fall back to sample data. */
  error?: string;
}

/**
 * Run a leads_list intent against GET /leads (and, for a score-filtered
 * query, GET /leads/{id}/score once per fetched lead).
 *
 * @param intent - The parsed leads_list intent.
 * @returns The results and whether they're real or mock.
 */
export async function runLeadsQuery(
  intent: Extract<ParsedIntent, { kind: "leads_list" }>,
): Promise<ChatQueryOutcome> {
  if (!hasApiToken()) {
    return { results: mockLeadsResults(intent), isMock: true };
  }

  try {
    const page = await apiFetch<LeadsApiResponse>("/leads", {
      searchParams: {
        status: intent.status,
        category: intent.category,
        min_confidence: intent.minConfidence,
        page: 1,
        page_size: LEADS_PAGE_SIZE,
      },
    });

    let rows: LeadResultRow[] = page.items.map(toLeadResultRow);

    // Approximation, not a true global filter -- GET /leads has no
    // score-based query param (score lookups are per-lead only). Scores
    // are checked across the page just fetched, capped at LEADS_PAGE_SIZE,
    // and the panel's response text says so explicitly.
    if (intent.minScorePercent !== undefined) {
      const threshold = intent.minScorePercent;
      const scored = await Promise.all(
        rows.map(async (row) => ({ row, scorePercent: await fetchLeadScorePercent(row.id) })),
      );
      rows = scored
        .filter(
          (entry): entry is { row: LeadResultRow; scorePercent: number } =>
            entry.scorePercent !== null && entry.scorePercent >= threshold,
        )
        .map(({ row, scorePercent }) => ({ ...row, scorePercent }));
    }

    return { results: { kind: "leads", rows }, isMock: false };
  } catch (error) {
    // Token was configured, so this was a real attempt that failed. Surface
    // the error for an explicit retry -- never silently swap in sample data.
    return { results: { kind: "leads", rows: [] }, isMock: false, error: describeError(error) };
  }
}

/**
 * Fetch one lead's current score as a whole-number percent.
 *
 * @param leadId - The lead to look up.
 * @returns The score as 0-100, or null if this lead has no computed score
 *   yet (GET /leads/{id}/score returns 404 in that case -- treated as "not
 *   yet scored", excluded from a score-filtered query, not a hard error).
 */
async function fetchLeadScorePercent(leadId: string): Promise<number | null> {
  try {
    const score = await apiFetch<LeadScoreApiResult>(`/leads/${leadId}/score`);
    return Math.round(score.total_score * 100);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      return null;
    }
    throw error;
  }
}

function toLeadResultRow(lead: LeadApiResult): LeadResultRow {
  return {
    id: lead.id,
    name: lead.name,
    category: lead.category,
    status: lead.status,
    confidencePercent: Math.round(lead.confidence_score * 100),
    scorePercent: null,
    contactEmail: lead.contact_email,
  };
}

/**
 * Run a places_search intent against POST /places/search.
 *
 * @param intent - The parsed places_search intent.
 * @returns The results and whether they're real or mock. The "no website"
 *   modifier is applied client-side to the real response -- Places search
 *   has no such filter, but PlaceSearchResultResponse already includes
 *   `website`, so no new backend logic is needed for this one.
 */
export async function runPlacesQuery(
  intent: Extract<ParsedIntent, { kind: "places_search" }>,
): Promise<ChatQueryOutcome> {
  if (!hasApiToken()) {
    return { results: mockPlacesResults(intent), isMock: true };
  }

  try {
    const response = await apiFetch<PlaceSearchApiResponse>("/places/search", {
      method: "POST",
      body: { industry: intent.industry, postal_code: intent.postalCode },
    });

    let rows: PlaceResultRow[] = response.results.map(toPlaceResultRow);
    if (intent.noWebsiteOnly) {
      rows = rows.filter((row) => !row.website);
    }

    return { results: { kind: "places", rows }, isMock: false };
  } catch (error) {
    // Real attempt failed -- surface the error for retry, no silent sample swap.
    return { results: { kind: "places", rows: [] }, isMock: false, error: describeError(error) };
  }
}

function toPlaceResultRow(place: PlaceApiResult): PlaceResultRow {
  return {
    id: place.place_id,
    name: place.display_name ?? "(unnamed)",
    address: place.formatted_address,
    website: place.website,
    phone: place.phone,
  };
}

function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    return error.status ? `API returned ${error.status}: ${error.message}` : error.message;
  }
  return "Unexpected error calling the API.";
}
