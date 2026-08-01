/**
 * Rule-based parser turning a plain-text chat request into a ParsedIntent.
 *
 * Deliberately not an LLM call -- the request this feature implements is
 * explicit that no new backend logic should be added, and a client-side
 * regex/keyword parser needs no new API cost-guard wiring, no prompt, and
 * is fully deterministic and debuggable. It is not full NLP: it recognizes
 * a fixed set of phrasings well and says so plainly when it doesn't
 * recognize something, rather than guessing.
 */

import type { LeadStatusFilter, ParsedIntent } from "@/types/chat";

const LEAD_STATUSES: readonly LeadStatusFilter[] = [
  "new",
  "enriching",
  "qualified",
  "contacted",
  "responded",
  "won",
  "lost",
  "disqualified",
];

/**
 * Best-effort city -> representative ZIP code lookup, so "Dallas" works the
 * same way "75201" would against POST /places/search (which only accepts a
 * postal_code, not a free-text city -- see backend/app/schemas/place.py).
 * Deliberately small and approximate: one representative ZIP per city, not
 * an exhaustive geocoder. Unrecognized cities get an honest "I don't know
 * that one, try a ZIP" response rather than a guess.
 */
const CITY_ZIP_LOOKUP: Readonly<Record<string, string>> = {
  dallas: "75201",
  austin: "78701",
  houston: "77002",
  "san antonio": "78205",
  "new york": "10001",
  "new york city": "10001",
  "los angeles": "90012",
  chicago: "60601",
  phoenix: "85003",
  philadelphia: "19102",
  "san diego": "92101",
  "san francisco": "94102",
  seattle: "98101",
  denver: "80202",
  boston: "02108",
  miami: "33130",
  atlanta: "30303",
  nashville: "37203",
  portland: "97201",
  charlotte: "28202",
  detroit: "48226",
};

const ZIP_PATTERN = /\b(\d{5})\b/;
const NO_WEBSITE_PATTERN =
  /\b(no website|without a website|missing a website|don'?t have a website)\b/;
const IN_LOCATION_PATTERN =
  /\b(?:find|search for|look for|show me)\s+(.+?)\s+in\s+([a-z0-9 ,.]+?)(?:\s+with\b|\s+that\b|$)/;
const SCORE_PATTERN = /\bscored?\s*(?:above|over|greater than|>)\s*(\d{1,3})/;
const CONFIDENCE_PATTERN = /\bconfidence\s*(?:above|over|greater than|>)\s*(\d{1,3})%?/;
const CATEGORY_PATTERN =
  /\bin the ([a-z0-9 &-]+?)\s+(?:industry|sector|category)\b|\bcategory[:\s]+([a-z0-9 &-]+)$/;

/** Extract one capturing group from a match, treating a missing/empty group
 * as absent rather than risking `undefined` leaking into a "number" field
 * elsewhere (this file runs under tsconfig's noUncheckedIndexedAccess, so
 * every RegExpMatchArray index is `string | undefined` by the type system,
 * regardless of whether the outer match already succeeded). */
function group(match: RegExpMatchArray | null, index: number): string | undefined {
  const value = match?.[index];
  return value && value.trim() ? value.trim() : undefined;
}

/**
 * Parse a plain-text request into a ParsedIntent.
 *
 * @param rawText - What the user typed into the chat panel.
 * @returns The parsed intent, or "unrecognized" with an explanatory message.
 */
export function parseChatQuery(rawText: string): ParsedIntent {
  const text = rawText.trim();
  if (!text) {
    return {
      kind: "unrecognized",
      message:
        'Type a request, e.g. "find restaurants in Dallas with no website" or ' +
        '"show me leads scored above 80".',
    };
  }

  const lower = text.toLowerCase();

  // "leads" anywhere in the request routes to GET /leads -- checked first
  // since a leads query never needs the "find X in Y" location parsing
  // below, and the word is an unambiguous signal either way.
  if (/\bleads?\b/.test(lower)) {
    return parseLeadsIntent(lower);
  }

  if (/\b(find|search for|look for|show me)\b/.test(lower)) {
    return parsePlacesIntent(lower);
  }

  return {
    kind: "unrecognized",
    message:
      'I didn\'t recognize that. Try "find <type of business> in <city or ZIP>" or ' +
      '"show me leads with status qualified".',
  };
}

function parseLeadsIntent(lower: string): ParsedIntent {
  const status = LEAD_STATUSES.find((candidate) => new RegExp(`\\b${candidate}\\b`).test(lower));

  const scoreRaw = group(lower.match(SCORE_PATTERN), 1);
  const minScorePercent = scoreRaw ? Number(scoreRaw) : undefined;

  const confidenceRaw = group(lower.match(CONFIDENCE_PATTERN), 1);
  const minConfidence = confidenceRaw ? Math.min(Number(confidenceRaw), 100) / 100 : undefined;

  const categoryMatch = lower.match(CATEGORY_PATTERN);
  const category = group(categoryMatch, 1) ?? group(categoryMatch, 2);

  return {
    kind: "leads_list",
    status,
    category,
    minConfidence,
    minScorePercent,
  };
}

function parsePlacesIntent(lower: string): ParsedIntent {
  const noWebsiteOnly = NO_WEBSITE_PATTERN.test(lower);

  const locationMatch = lower.match(IN_LOCATION_PATTERN);
  const industry = group(locationMatch, 1);
  const locationRaw = group(locationMatch, 2);

  if (!industry || !locationRaw) {
    return {
      kind: "unrecognized",
      message:
        'Tell me what to look for and where, e.g. "find plumbers in Austin" or ' +
        '"find dental clinics in 78701".',
    };
  }

  const zipMatch = group(locationRaw.match(ZIP_PATTERN), 1);
  if (zipMatch) {
    return {
      kind: "places_search",
      industry,
      postalCode: zipMatch,
      locationLabel: locationRaw,
      noWebsiteOnly,
    };
  }

  const lookupKey = locationRaw.replace(/\.$/, "").trim();
  const zip = CITY_ZIP_LOOKUP[lookupKey];
  if (zip) {
    return {
      kind: "places_search",
      industry,
      postalCode: zip,
      locationLabel: locationRaw,
      noWebsiteOnly,
    };
  }

  return {
    kind: "unrecognized",
    message: `I don't have a ZIP code on file for "${locationRaw}". Try including a ZIP code, e.g. "${industry} in 75201".`,
  };
}
