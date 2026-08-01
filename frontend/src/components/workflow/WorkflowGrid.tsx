import { AlertTriangle, CheckCircle2, CircleDashed, Clock, Loader2, Table2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { PanelHeader } from "@/components/dashboard/PanelHeader";
import { WorkflowCellPanel } from "@/components/workflow/WorkflowCellPanel";
import type {
  PipelineStepKey,
  PipelineStepMeta,
  StepStatus,
  WorkflowLeadRow,
} from "@/types/workflow";

interface WorkflowGridProps {
  steps: PipelineStepMeta[];
  rows: WorkflowLeadRow[];
}

interface ActivePanelState {
  row: WorkflowLeadRow;
  step: PipelineStepMeta;
}

const STATUS_STYLES: Record<StepStatus, string> = {
  done: "border-emerald-200 bg-emerald-50 text-emerald-700",
  in_progress: "border-sky-200 bg-sky-50 text-sky-700",
  pending: "border-amber-200 bg-amber-50 text-amber-700",
  failed: "border-rose-200 bg-rose-50 text-rose-700",
  not_started: "border-slate-200 bg-slate-50 text-slate-400",
};

const STATUS_LABELS: Record<StepStatus, string> = {
  done: "Done",
  in_progress: "Running",
  pending: "Pending",
  failed: "Failed",
  not_started: "Not started",
};

const CELL_ICON: Record<StepStatus, typeof CheckCircle2> = {
  done: CheckCircle2,
  in_progress: Loader2,
  pending: Clock,
  failed: AlertTriangle,
  not_started: CircleDashed,
};

/** How long a simulated re-run takes before flipping back to "done". Mock
 * timing only -- a real wiring would await the actual POST response. */
const SIMULATED_RERUN_MS = 900;

/**
 * Spreadsheet-style workflow grid: one row per lead, one column per pipeline
 * step (discovered -> enriched -> audited -> scored -> drafted -> approved
 * -> sent -> replied). Read-only for now, per this phase's scope -- no
 * drag-drop column builder, just visibility plus a per-cell action:
 *
 * - "rerun" columns (enriched/audited/scored/drafted) simulate re-running
 *   that step for that lead: the cell flips to "Running" then back to
 *   "Done" a moment later. This is local UI state only in this phase (see
 *   mockWorkflowData.ts's module docstring) -- wiring it to the real
 *   per-lead endpoints (POST /leads/{id}/email/enrich, POST /audits, POST
 *   /leads/{id}/score, POST /outreach/leads/{id}/drafts) is a later phase.
 * - "review" columns (approved/sent) open a read-only preview panel instead
 *   of firing an action -- these are gated by AGENTS.md section 8's human-
 *   approval rule, so a grid cell must never one-click approve or send.
 * - "detail" columns (discovered/replied) open the same panel read-only;
 *   there is nothing to re-run for an origin event or an inbound reply.
 *
 * Named export per AGENTS.md section 4.1.
 */
export function WorkflowGrid({ steps, rows: initialRows }: WorkflowGridProps): JSX.Element {
  const [rows, setRows] = useState<WorkflowLeadRow[]>(initialRows);
  const [activePanel, setActivePanel] = useState<ActivePanelState | null>(null);
  const pendingTimeouts = useRef<Set<number>>(new Set());

  // Mock-only cleanup: cancel any simulated re-run still in flight if the
  // grid unmounts before it "finishes", so we never set state after unmount.
  useEffect(() => {
    const timeouts = pendingTimeouts.current;
    return () => {
      timeouts.forEach((timeoutId) => window.clearTimeout(timeoutId));
      timeouts.clear();
    };
  }, []);

  function handleCellClick(row: WorkflowLeadRow, step: PipelineStepMeta): void {
    if (step.interaction === "rerun") {
      simulateRerun(row.id, step.key);
      return;
    }
    setActivePanel({ row, step });
  }

  function simulateRerun(rowId: string, stepKey: PipelineStepKey): void {
    setRows((previous) =>
      previous.map((row) =>
        row.id === rowId
          ? {
              ...row,
              steps: {
                ...row.steps,
                [stepKey]: { ...row.steps[stepKey], status: "in_progress" },
              },
            }
          : row,
      ),
    );

    const timeoutId: number = window.setTimeout(() => {
      pendingTimeouts.current.delete(timeoutId);
      setRows((previous) =>
        previous.map((row) =>
          row.id === rowId
            ? {
                ...row,
                steps: {
                  ...row.steps,
                  [stepKey]: {
                    status: "done",
                    detail: "Re-run just now",
                    timestampLabel: "Just now",
                  },
                },
              }
            : row,
        ),
      );
    }, SIMULATED_RERUN_MS);
    pendingTimeouts.current.add(timeoutId);
  }

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <PanelHeader
        icon={<Table2 className="h-4 w-4 text-slate-500" aria-hidden />}
        title="Lead Workflow"
      />

      <div className="max-h-[70vh] overflow-auto rounded-lg border border-slate-100">
        <table className="w-full min-w-[960px] border-collapse text-left text-xs">
          <thead>
            <tr>
              <th className="sticky left-0 top-0 z-20 border-b border-r border-slate-200 bg-slate-50 px-3 py-2 font-medium text-slate-500">
                Lead
              </th>
              {steps.map((step) => (
                <th
                  key={step.key}
                  className="sticky top-0 z-10 border-b border-slate-200 bg-slate-50 px-3 py-2 text-center font-medium text-slate-500"
                >
                  {step.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id} className="border-b border-slate-100 last:border-b-0">
                <td className="sticky left-0 z-10 border-r border-slate-200 bg-white px-3 py-2">
                  <div className="font-medium text-slate-700">{row.leadName}</div>
                  <div className="text-[11px] text-slate-400">{row.category}</div>
                </td>
                {steps.map((step) => {
                  const cell = row.steps[step.key];
                  const Icon = CELL_ICON[cell.status];
                  return (
                    <td key={step.key} className="px-2 py-2 text-center align-middle">
                      <button
                        type="button"
                        onClick={() => handleCellClick(row, step)}
                        title={`${step.label}: ${STATUS_LABELS[cell.status]}${
                          cell.detail ? ` -- ${cell.detail}` : ""
                        }`}
                        className={`inline-flex w-full items-center justify-center gap-1.5 rounded-md border px-2 py-1.5 transition-colors hover:brightness-95 ${STATUS_STYLES[cell.status]}`}
                      >
                        <Icon
                          className={`h-3.5 w-3.5 ${cell.status === "in_progress" ? "animate-spin" : ""}`}
                          aria-hidden
                        />
                        <span className="whitespace-nowrap">{STATUS_LABELS[cell.status]}</span>
                      </button>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {activePanel ? (
        <WorkflowCellPanel
          row={activePanel.row}
          step={activePanel.step}
          onClose={() => setActivePanel(null)}
        />
      ) : null}
    </section>
  );
}
