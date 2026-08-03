import { BarChart3, DollarSign, TrendingUp } from "lucide-react";
import Head from "next/head";
import { useCallback, useEffect, useState } from "react";

import { ErrorState } from "@/components/common/ErrorState";
import { PanelHeader } from "@/components/dashboard/PanelHeader";
import { AppShell } from "@/components/layout/AppShell";
import { fetchAnalytics, type AnalyticsOutcome, type RankedItem } from "@/lib/analyticsApi";
import { formatRate } from "@/lib/sampleSize";

/**
 * Analytics page: the existing Phase 7 analytics API surfaced as a real
 * screen -- GET /analytics/overview, /analytics/top-industries,
 * /analytics/top-services, and /analytics/cost-status (see
 * lib/analyticsApi.ts). Replaces the earlier "Coming Soon" placeholder.
 * Falls back to a clearly-labeled illustrative dataset when no dev session
 * is available, and to an explicit error+retry (never silent samples) when
 * a real call fails. A default export is required here (Next.js page file).
 */
// eslint-disable-next-line import/no-default-export -- Next.js requires a default export here.
export default function AnalyticsPage(): JSX.Element {
  const [outcome, setOutcome] = useState<AnalyticsOutcome | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const load = useCallback(async (): Promise<void> => {
    setIsLoading(true);
    const result = await fetchAnalytics();
    setOutcome(result);
    setIsLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <AppShell>
      <Head>
        <title>Everen BD Agent -- Analytics</title>
      </Head>
      <div className="mb-4 flex items-center gap-2">
        <BarChart3 className="h-5 w-5 text-slate-600" aria-hidden />
        <h1 className="text-lg font-semibold text-slate-800">Analytics</h1>
      </div>

      {outcome?.isMock ? (
        <div className="mb-4 rounded-md bg-amber-50 px-3 py-2 text-xs font-medium uppercase tracking-wide text-amber-700">
          Sample data -- not a real query. No local dev session available (is the backend
          running at NEXT_PUBLIC_API_BASE_URL?).
        </div>
      ) : null}

      {isLoading && !outcome ? (
        <p className="text-sm text-slate-400">Loading analytics&hellip;</p>
      ) : outcome?.error ? (
        <ErrorState message={outcome.error} onRetry={() => void load()} />
      ) : outcome ? (
        <div className="flex flex-col gap-4">
          <OverviewCards outcome={outcome} />

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <RankedListPanel title="Top industries (by won deals)" items={outcome.topIndustries} />
            <RankedListPanel title="Top services (by won deals)" items={outcome.topServices} />
          </div>

          {outcome.costStatus ? <CostStatusPanel providers={outcome.costStatus} /> : null}
        </div>
      ) : null}
    </AppShell>
  );
}

function OverviewCards({ outcome }: { outcome: AnalyticsOutcome }): JSX.Element {
  const { overview } = outcome;
  const replyRatePercent = Math.round(overview.replyRate * 100);
  const openRatePercent = Math.round(overview.openRate * 100);

  const cards: { id: string; label: string; value: string }[] = [
    { id: "emails-sent", label: "Emails sent", value: String(overview.emailsSent) },
    {
      id: "open-rate",
      label: "Open rate",
      value: formatRate(openRatePercent, overview.emailsSent, "emails"),
    },
    {
      id: "reply-rate",
      label: "Reply rate",
      value: formatRate(replyRatePercent, overview.emailsSent, "emails"),
    },
    { id: "meetings", label: "Meetings booked", value: String(overview.meetingsBooked) },
    { id: "won", label: "Deals won", value: String(overview.dealsWon) },
  ];

  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
      {cards.map((card) => (
        <div key={card.id} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <p className="text-xs font-medium text-slate-500">{card.label}</p>
          <p className="mt-2 text-2xl font-semibold text-slate-900">{card.value}</p>
        </div>
      ))}
    </div>
  );
}

function RankedListPanel({ title, items }: { title: string; items: RankedItem[] }): JSX.Element {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <PanelHeader icon={<TrendingUp className="h-4 w-4 text-slate-500" aria-hidden />} title={title} />
      {items.length === 0 ? (
        <p className="text-xs text-slate-400">No won deals yet -- this fills in as deals close.</p>
      ) : (
        <ul className="space-y-2">
          {items.map((item, index) => (
            <li key={item.label} className="flex items-center justify-between text-sm">
              <span className="flex items-center gap-2 text-slate-700">
                <span className="flex h-5 w-5 items-center justify-center rounded-full bg-slate-100 text-[11px] font-semibold text-slate-500">
                  {index + 1}
                </span>
                {item.label}
              </span>
              <span className="font-semibold text-slate-800">{item.count}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function CostStatusPanel({
  providers,
}: {
  providers: NonNullable<AnalyticsOutcome["costStatus"]>;
}): JSX.Element {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <PanelHeader
        icon={<DollarSign className="h-4 w-4 text-slate-500" aria-hidden />}
        title="Today's API budget standing"
      />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {providers.map((provider) => {
          const percent = Math.min(100, Math.round(provider.fractionSpent * 100));
          const barColor = provider.exhausted
            ? "bg-rose-500"
            : provider.pastAlertThreshold
              ? "bg-amber-500"
              : "bg-emerald-500";
          return (
            <div key={provider.provider} className="rounded-lg border border-slate-100 p-3">
              <div className="flex items-center justify-between text-xs">
                <span className="font-medium capitalize text-slate-700">{provider.provider}</span>
                <span className="text-slate-500">
                  ${provider.spentUsd.toFixed(2)} / ${provider.dailyBudgetUsd.toFixed(2)}
                </span>
              </div>
              <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-slate-100">
                <div className={`h-full ${barColor}`} style={{ width: `${percent}%` }} />
              </div>
              {provider.exhausted ? (
                <p className="mt-1 text-[11px] font-medium text-rose-600">
                  Budget exhausted -- calls to this provider are paused today.
                </p>
              ) : provider.pastAlertThreshold ? (
                <p className="mt-1 text-[11px] font-medium text-amber-600">
                  Past the 80% alert threshold.
                </p>
              ) : null}
            </div>
          );
        })}
      </div>
    </section>
  );
}
