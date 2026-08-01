import { Building2 } from "lucide-react";
import Head from "next/head";

import { AppShell } from "@/components/layout/AppShell";
import { ChatPanel } from "@/components/dashboard/ChatPanel";
import { FollowUpTracker } from "@/components/dashboard/FollowUpTracker";
import { KanbanFunnel } from "@/components/dashboard/KanbanFunnel";
import { KpiCardRow } from "@/components/dashboard/KpiCardRow";
import { PartnerOutreachPanel } from "@/components/dashboard/PartnerOutreachPanel";
import { WorkflowNodeBuilder } from "@/components/dashboard/WorkflowNodeBuilder";
import {
  followUpRows,
  funnelColumns,
  kpiMetrics,
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
 * Renders mock data only (see src/lib/mockDashboardData.ts); this phase is a
 * design/layout pass, confirmed with the user before building. A default
 * export is required here because this is a Next.js page file (framework
 * requirement, the one sanctioned exception to AGENTS.md section 4.1's
 * named-exports rule -- see also src/pages/_app.tsx).
 */
// eslint-disable-next-line import/no-default-export -- Next.js requires a default export here.
export default function DashboardPage(): JSX.Element {
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
        <KpiCardRow metrics={kpiMetrics} />

        <ChatPanel />

        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1.6fr_1fr]">
          <KanbanFunnel columns={funnelColumns} />
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
