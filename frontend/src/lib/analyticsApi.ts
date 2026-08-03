/**
 * Real analytics data for the Analytics page.
 *
 * Calls the existing Phase 7 analytics API -- GET /analytics/overview,
 * /analytics/top-industries, /analytics/top-services, and
 * /analytics/cost-status (see backend/app/api/v1/analytics.py) -- in
 * parallel. Falls back to a small illustrative mock dataset only when no
 * dev session is available; a real call that fails surfaces an explicit
 * error instead of silently swapping in samples, same as every other real
 * data source in this app.
 *
 * cost-status is admin-only server-side (require_admin) -- the local dev
 * session mints an admin role by default (see backend/app/core/dev_auth.py),
 * so this works out of the box locally. A real non-admin token would get a
 * 403 on just that one call; handled by treating it as "no cost data"
 * rather than failing the whole page.
 */

import { apiFetch, ApiError, hasApiToken } from "@/lib/apiClient";

export interface OverviewMetrics {
  emailsSent: number;
  opens: number;
  openRate: number;
  replies: number;
  replyRate: number;
  meetingsBooked: number;
  dealsWon: number;
}

export interface RankedItem {
  label: string;
  count: number;
}

export interface ProviderBudget {
  provider: string;
  dailyBudgetUsd: number;
  spentUsd: number;
  remainingUsd: number;
  fractionSpent: number;
  pastAlertThreshold: boolean;
  exhausted: boolean;
}

export interface AnalyticsOutcome {
  overview: OverviewMetrics;
  topIndustries: RankedItem[];
  topServices: RankedItem[];
  costStatus: ProviderBudget[] | null;
  isMock: boolean;
  error?: string;
}

interface OverviewApiResponse {
  emails_sent: number;
  opens: number;
  open_rate: number;
  replies: number;
  reply_rate: number;
  meetings_booked: number;
  deals_won: number;
}

interface RankedItemApiResult {
  label: string;
  count: number;
}

interface TopListApiResponse {
  items: RankedItemApiResult[];
}

interface ProviderBudgetApiResult {
  provider: string;
  daily_budget_usd: number;
  spent_usd: number;
  remaining_usd: number;
  fraction_spent: number;
  past_alert_threshold: boolean;
  exhausted: boolean;
}

interface CostStatusApiResponse {
  providers: ProviderBudgetApiResult[];
}

const MOCK_OUTCOME: Omit<AnalyticsOutcome, "isMock" | "error"> = {
  overview: {
    emailsSent: 184,
    opens: 61,
    openRate: 0.33,
    replies: 22,
    replyRate: 0.12,
    meetingsBooked: 5,
    dealsWon: 3,
  },
  topIndustries: [
    { label: "Restaurants", count: 4 },
    { label: "Home Services", count: 3 },
    { label: "Retail", count: 2 },
  ],
  topServices: [
    { label: "Website redesign + local SEO", count: 4 },
    { label: "Managed Engineering Retainer", count: 2 },
  ],
  costStatus: [
    {
      provider: "places",
      dailyBudgetUsd: 20,
      spentUsd: 3.2,
      remainingUsd: 16.8,
      fractionSpent: 0.16,
      pastAlertThreshold: false,
      exhausted: false,
    },
    {
      provider: "openai",
      dailyBudgetUsd: 20,
      spentUsd: 1.1,
      remainingUsd: 18.9,
      fractionSpent: 0.055,
      pastAlertThreshold: false,
      exhausted: false,
    },
  ],
};

function toRankedItems(response: TopListApiResponse): RankedItem[] {
  return response.items.map((i) => ({ label: i.label, count: i.count }));
}

/**
 * Fetch every metric the Analytics page shows, in parallel.
 *
 * @returns Overview, top industries/services, and (if the caller is admin)
 *   cost status.
 */
export async function fetchAnalytics(): Promise<AnalyticsOutcome> {
  if (!(await hasApiToken())) {
    return { ...MOCK_OUTCOME, isMock: true };
  }

  try {
    const [overviewResponse, industriesResponse, servicesResponse] = await Promise.all([
      apiFetch<OverviewApiResponse>("/analytics/overview"),
      apiFetch<TopListApiResponse>("/analytics/top-industries"),
      apiFetch<TopListApiResponse>("/analytics/top-services"),
    ]);

    let costStatus: ProviderBudget[] | null = null;
    try {
      const costResponse = await apiFetch<CostStatusApiResponse>("/analytics/cost-status");
      costStatus = costResponse.providers.map((p) => ({
        provider: p.provider,
        dailyBudgetUsd: p.daily_budget_usd,
        spentUsd: p.spent_usd,
        remainingUsd: p.remaining_usd,
        fractionSpent: p.fraction_spent,
        pastAlertThreshold: p.past_alert_threshold,
        exhausted: p.exhausted,
      }));
    } catch (costError) {
      // Admin-only -- a 403 for a non-admin caller means "no cost panel",
      // not a failure of the whole page.
      if (!(costError instanceof ApiError && costError.status === 403)) {
        throw costError;
      }
    }

    return {
      overview: {
        emailsSent: overviewResponse.emails_sent,
        opens: overviewResponse.opens,
        openRate: overviewResponse.open_rate,
        replies: overviewResponse.replies,
        replyRate: overviewResponse.reply_rate,
        meetingsBooked: overviewResponse.meetings_booked,
        dealsWon: overviewResponse.deals_won,
      },
      topIndustries: toRankedItems(industriesResponse),
      topServices: toRankedItems(servicesResponse),
      costStatus,
      isMock: false,
    };
  } catch (error) {
    return {
      overview: {
        emailsSent: 0,
        opens: 0,
        openRate: 0,
        replies: 0,
        replyRate: 0,
        meetingsBooked: 0,
        dealsWon: 0,
      },
      topIndustries: [],
      topServices: [],
      costStatus: null,
      isMock: false,
      error: describeError(error),
    };
  }
}

function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    return error.status ? `API returned ${error.status}: ${error.message}` : error.message;
  }
  return "Unexpected error calling the API.";
}
