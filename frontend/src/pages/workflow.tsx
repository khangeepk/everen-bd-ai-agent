import { Table2 } from "lucide-react";
import Head from "next/head";

import { AppShell } from "@/components/layout/AppShell";
import { WorkflowGrid } from "@/components/workflow/WorkflowGrid";
import { pipelineSteps, workflowRows } from "@/lib/mockWorkflowData";

/**
 * Lead Workflow page: a spreadsheet-style view of every lead's progress
 * through the pipeline (discovered -> enriched -> audited -> scored ->
 * drafted -> approved -> sent -> replied), one row per lead, one column per
 * step. Renders mock data only (see src/lib/mockWorkflowData.ts) -- this
 * phase is a design/layout pass, confirmed with the user before wiring to
 * the real backend. A default export is required here because this is a
 * Next.js page file (the one sanctioned exception to AGENTS.md section
 * 4.1's named-exports rule -- see also src/pages/_app.tsx).
 */
// eslint-disable-next-line import/no-default-export -- Next.js requires a default export here.
export default function WorkflowPage(): JSX.Element {
  return (
    <AppShell>
      <Head>
        <title>Everen BD Agent -- Workflow</title>
      </Head>

      <div className="mb-4 flex items-center gap-2">
        <Table2 className="h-5 w-5 text-slate-600" aria-hidden />
        <h1 className="text-lg font-semibold text-slate-800">Lead workflow</h1>
      </div>

      <WorkflowGrid steps={pipelineSteps} rows={workflowRows} />
    </AppShell>
  );
}
