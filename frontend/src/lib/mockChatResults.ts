/**
 * Fallback sample results for the chat panel.
 *
 * Used when no NEXT_PUBLIC_DEV_API_TOKEN is configured, or when a real call
 * fails (network error, 401, etc.) -- see src/lib/chatQueries.ts. Results
 * are filtered/labeled to loosely respect the parsed intent so a fallback
 * still reads as an answer to what was actually asked, not generic filler.
 */

import type { ChatResults, LeadResultRow, ParsedIntent, PlaceResultRow } from "@/types/chat";

const SAMPLE_LEADS: readonly LeadResultRow[] = [
  {
    id: "mock-lead-1",
    name: "Northgate Manufacturing",
    category: "Manufacturing",
    status: "qualified",
    confidencePercent: 82,
    scorePercent: 85,
    contactEmail: "ops@northgatemfg.example",
  },
  {
    id: "mock-lead-2",
    name: "Blue Ridge Logistics",
    category: "Logistics",
    status: "new",
    confidencePercent: 54,
    scorePercent: 34,
    contactEmail: null,
  },
  {
    id: "mock-lead-3",
    name: "Summit Foods",
    category: "Food & Beverage",
    status: "contacted",
    confidencePercent: 71,
    scorePercent: null,
    contactEmail: "hello@summitfoods.example",
  },
  {
    id: "mock-lead-4",
    name: "Horizon Retail",
    category: "Retail",
    status: "qualified",
    confidencePercent: 88,
    scorePercent: 91,
    contactEmail: "team@horizonretail.example",
  },
  {
    id: "mock-lead-5",
    name: "Cedar Grove Realty",
    category: "Real Estate",
    status: "responded",
    confidencePercent: 63,
    scorePercent: 77,
    contactEmail: "info@cedargroverealty.example",
  },
];

const SAMPLE_PLACES: readonly PlaceResultRow[] = [
  { id: "mock-place-1", name: "Example Business A", address: "123 Main St", website: "https://example-a.test", phone: "(555) 010-0001" },
  { id: "mock-place-2", name: "Example Business B", address: "456 Oak Ave", website: null, phone: "(555) 010-0002" },
  { id: "mock-place-3", name: "Example Business C", address: "789 Pine Rd", website: null, phone: null },
];

/**
 * Sample leads matching a leads_list intent as closely as the mock data allows.
 *
 * @param intent - The parsed leads_list intent.
 * @returns Sample results, filtered by status/confidence/score the same way
 *   the real query would be.
 */
export function mockLeadsResults(intent: Extract<ParsedIntent, { kind: "leads_list" }>): ChatResults {
  let rows = [...SAMPLE_LEADS];

  if (intent.status) {
    const status = intent.status;
    rows = rows.filter((row) => row.status === status);
  }
  if (intent.minConfidence !== undefined) {
    const threshold = intent.minConfidence;
    rows = rows.filter((row) => row.confidencePercent / 100 >= threshold);
  }
  if (intent.minScorePercent !== undefined) {
    const threshold = intent.minScorePercent;
    rows = rows.filter((row) => row.scorePercent !== null && row.scorePercent >= threshold);
  }

  return { kind: "leads", rows };
}

/**
 * Sample places matching a places_search intent as closely as the mock data allows.
 *
 * @param intent - The parsed places_search intent.
 * @returns Sample results, labeled with the requested industry/location and
 *   filtered by the no-website modifier the same way the real query would be.
 */
export function mockPlacesResults(
  intent: Extract<ParsedIntent, { kind: "places_search" }>,
): ChatResults {
  const labeledIndustry = capitalize(intent.industry);
  const base = SAMPLE_PLACES.map((row, index) => ({
    ...row,
    id: `mock-place-${intent.postalCode}-${index}`,
    name: `${labeledIndustry} ${String.fromCharCode(65 + index)} (${intent.locationLabel})`,
  }));
  const rows = intent.noWebsiteOnly ? base.filter((row) => !row.website) : base;

  return { kind: "places", rows };
}

function capitalize(text: string): string {
  const [first, ...rest] = text;
  if (!first) {
    return text;
  }
  return first.toUpperCase() + rest.join("");
}
