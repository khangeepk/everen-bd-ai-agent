import { Filter } from "lucide-react";

import { ComplianceNotice } from "@/components/common/ComplianceNotice";
import { LeadScoreBadge } from "@/components/common/LeadScoreBadge";
import { StageChip } from "@/components/common/StageChip";
import { PanelHeader } from "@/components/dashboard/PanelHeader";
import type { PipelineStage } from "@/lib/plainLanguage";
import type { FunnelColumn } from "@/types/dashboard";

interface KanbanFunnelProps {
  columns: FunnelColumn[];
}

/** Column id -> backend pipeline stage, so each column header gets a tooltip. */
const COLUMN_STAGE: Record<string, PipelineStage> = {
  prospecting: "new",
  qualification: "contacted",
  proposal: "interested",
  negotiation: "hot",
  won: "converted",
  lost: "lost",
};

/**
 * Deal-stage Kanban board. Read-only in this phase (no drag/drop wired up
 * yet -- that needs a real backend pipeline mutation to be meaningful, see
 * backend/app/services/pipeline.py). Named export per AGENTS.md section 4.1.
 */
export function KanbanFunnel({ columns }: KanbanFunnelProps): JSX.Element {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <PanelHeader icon={<Filter className="h-4 w-4 text-slate-500" aria-hidden />} title="Interactive Kanban Funnel" />

      <div className="grid grid-cols-2 gap-3 overflow-x-auto sm:grid-cols-3 lg:grid-cols-6">
        {columns.map((column) => (
          <div key={column.id} className="min-w-[150px] rounded-lg bg-slate-50 p-2">
            <div className="mb-2">
              {COLUMN_STAGE[column.id] ? (
                <StageChip stage={COLUMN_STAGE[column.id] as PipelineStage} />
              ) : (
                <p className="truncate text-xs font-semibold text-slate-600">{column.title}</p>
              )}
            </div>
            <div className="flex flex-col gap-2">
              {column.deals.map((deal) => (
                <div
                  key={deal.id}
                  className="rounded-md border border-slate-200 bg-white p-2 text-xs shadow-sm"
                >
                  <p className="truncate font-medium text-slate-800" title={deal.accountName}>
                    {deal.accountName}
                  </p>
                  {deal.score !== undefined ? (
                    <div className="mt-1">
                      <LeadScoreBadge
                        score={deal.score}
                        reasons={deal.scoreReasons ?? []}
                        doNotContact={deal.complianceState !== undefined}
                      />
                    </div>
                  ) : null}
                  <p className="mt-1 text-slate-500">
                    Deal value <span className="font-semibold text-slate-700">{deal.dealValueLabel}</span>
                  </p>
                  {deal.complianceState ? (
                    <div className="mt-1.5">
                      <ComplianceNotice state={deal.complianceState} />
                    </div>
                  ) : null}
                </div>
              ))}
              {column.deals.length === 0 ? (
                <p className="rounded-md border border-dashed border-slate-200 p-2 text-center text-[11px] text-slate-400">
                  No deals
                </p>
              ) : null}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
