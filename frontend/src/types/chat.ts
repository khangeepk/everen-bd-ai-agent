/**
 * Shared shapes for the dashboard chat panel: intent parsing, raw API
 * response shapes, and the normalized result rows the table renders.
 *
 * Unlike src/types/dashboard.ts and src/types/workflow.ts, this module
 * backs a genuinely wired feature (see src/lib/apiClient.ts) -- the API*
 * types below are deliberately a minimal subset of the real Pydantic
 * response schemas (backend/app/schemas/lead.py's LeadResponse,
 * backend/app/schemas/lead_score.py's LeadScoreResponse,
 * backend/app/schemas/place.py's PlaceSearchResponse), carrying only the
 * fields this feature actually reads.
 */

/** The 8 LeadStatus values from backend/app/db/models/lead.py, lowercase to
 * match the API's wire format. */
export type LeadStatusFilter =
  | "new"
  | "enriching"
  | "qualified"
  | "contacted"
  | "responded"
  | "won"
  | "lost"
  | "disqualified";

/** What the parser understood a typed request to mean. */
export type ParsedIntent =
  | {
      kind: "places_search";
      industry: string;
      postalCode: string;
      locationLabel: string;
      noWebsiteOnly: boolean;
    }
  | {
      kind: "leads_list";
      status?: LeadStatusFilter;
      category?: string;
      minConfidence?: number;
      minScorePercent?: number;
    }
  | { kind: "unrecognized"; message: string };

// ---------------------------------------------------------------------------
// Raw API response shapes (minimal subsets -- see this file's docstring)
// ---------------------------------------------------------------------------

/** Subset of backend LeadResponse. */
export interface LeadApiResult {
  id: string;
  name: string;
  category: string | null;
  status: string;
  confidence_score: number;
  contact_email: string | null;
  website: string | null;
}

/** Subset of backend PaginatedLeads. */
export interface LeadsApiResponse {
  items: LeadApiResult[];
  total: number;
}

/** Subset of backend LeadScoreResponse -- total_score is 0.0-1.0. */
export interface LeadScoreApiResult {
  total_score: number;
}

/** Subset of backend PlaceSearchResultResponse. */
export interface PlaceApiResult {
  place_id: string;
  display_name: string | null;
  formatted_address: string | null;
  website: string | null;
  phone: string | null;
}

/** Subset of backend PlaceSearchResponse. */
export interface PlaceSearchApiResponse {
  results: PlaceApiResult[];
  total_found: number;
}

// ---------------------------------------------------------------------------
// Normalized rows the table renders, regardless of where they came from
// (a real API call or the mock fallback)
// ---------------------------------------------------------------------------

/** One row of a leads-query result table. */
export interface LeadResultRow {
  id: string;
  name: string;
  category: string | null;
  status: string;
  confidencePercent: number;
  /** Null when this lead has no computed score yet -- excluded from a
   * score-filtered query rather than treated as a non-match either way. */
  scorePercent: number | null;
  contactEmail: string | null;
}

/** One row of a places-query result table. */
export interface PlaceResultRow {
  id: string;
  name: string;
  address: string | null;
  website: string | null;
  phone: string | null;
}

/** The rendered outcome of one query, tagged by which endpoint it came from. */
export type ChatResults =
  | { kind: "leads"; rows: LeadResultRow[] }
  | { kind: "places"; rows: PlaceResultRow[] };

/** One turn in the chat panel's message history. */
export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  results?: ChatResults;
  /** True when `results` came from the mock fallback rather than a real API
   * call -- surfaced in the UI so a sample result is never mistaken for a
   * real one. See src/lib/chatQueries.ts. */
  isMock?: boolean;
}
