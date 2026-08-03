import { Building2 } from "lucide-react";
import Head from "next/head";
import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/layout/AppShell";
import { ErrorState } from "@/components/common/ErrorState";
import { ChatPanel } from "@/components/dashboard/ChatPanel";
import { FollowUpTracker } from "@/components/dashboard/FollowUpTracker";
import { KanbanFunnel } from "@/components/dashboard/KanbanFunnel";
import { KpiCardRow } from "@/components/dashboard/KpiCardRow";
import { NextActionBanner } from "@/components/dashboard/NextActionBanner";
import { PartnerOutreachPanel } from "@/components/dashboard/PartnerOutreachPanel";
import { WorkflowNodeBuilder } from "@/components/dashboard/WorkflowNodeBuilder";
import { fetchDashboardSummary, type DashboardSummaryOutcome } from "@/lib/dashboardApi";
import {
  followUpRows,
  outreachVolume,
  partnerLocations,
  recentWorkflows,
  responseRateSlices,
  workflowCanvasEdges,
  workflowCanvasNodes,
  workflowLibrary,
} from "@/lib/mockDashboardData";

/**
 * B2B Deal Flow dashboard -- the main landing page.
 *
 * KPI cards, the Kanban funnel, and the "what should I do now?" banner are
 * wired to the real GET /analytics/dashboard-summary (see
 * lib/dashboardApi.ts) when a dev session is available -- status counts,
 * pipeline stage groupings, and next-action counts all reflect real seeded
 * data, not the mock dataset. The follow-up tracker, partner-outreach
 * panel, and workflow builder below remain mock/design-pass data (see
 * lib/mockDashboardData.ts) -- they weren't part of this wiring pass.
 * A default export is required here (Next.js page file).
 */
// eslint-disable-next-line import/no-default-export -- Next.js requires a default export here.
export default function DashboardPage(): JSX.Element {
  const [summary, setSummary] = useState<DashboardSummaryOutcome | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const load = useCallback(async (): Promise<void> => {
    setIsLoading(true);
    const result = await fetchDashboardSummary();
    setSummary(result);
    setIsLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <AppShell>
      <Head>
        <title>Everen BD Agent -- Dashboard</title>
      </Head>

      <div className="mb-4 flex items-center gap-2">
        <Building2 className="h-5 w-5 text-slate-600" aria-hidden />
        <h1 className="text-lg font-semibold text-slate-800">B2B deal flow</h1>
      </div>

      <div className="flex flex-col gap-4">
        {summary?.isMock ? (
          <div className="rounded-md bg-amber-50 px-3 py-2 text-xs font-medium uppercase tracking-wide text-amber-700">
            Sample data below -- not real query. No local dev session available (is the backend
            running at NEXT_PUBLIC_API_BASE_URL?).
          </div>
        ) : null}

        {isLoading && !summary ? (
          <p className="text-sm text-slate-400">Loading dashboard&hellip;</p>
        ) : summary?.error ? (
          <ErrorState message={summary.error} onRetry={() => void load()} />
        ) : summary ? (
          <>
            <NextActionBanner counts={summary.pendingWork} />
            <KpiCardRow metrics={summary.kpis} />
          </>
        ) : null}

        <ChatPanel />

        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1.6fr_1fr]">
          {summary && !summary.error ? <KanbanFunnel columns={summary.kanbanColumns} /> : null}
          <PartnerOutreachPanel
            volume={outreachVolume}
            responseRate={responseRateSlices}
            locations={partnerLocations}
          />
        </div>

        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <FollowUpTracker rows={followUpRows} />
          <WorkflowNodeBuilder
            library={workflowLibrary}
            nodes={workflowCanvasNodes}
            edges={workflowCanvasEdges}
            recentWorkflows={recentWorkflows}
          />
        </div>
      </div>
    </AppShell>
  );
}
