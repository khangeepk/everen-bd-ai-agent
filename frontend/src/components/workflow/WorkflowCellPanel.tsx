import { X } from "lucide-react";

import type { PipelineStepMeta, StepStatus, WorkflowLeadRow } from "@/types/workflow";

interface WorkflowCellPanelProps {
  row: WorkflowLeadRow;
  step: PipelineStepMeta;
  onClose: () => void;
}

const STATUS_LABELS: Record<StepStatus, string> = {
  done: "Done",
  in_progress: "Running",
  pending: "Pending",
  failed: "Failed",
  not_started: "Not started",
};

/**
 * Read-only preview panel opened by a "review" or "detail" cell click.
 *
 * Deliberately never fires an approve/send/etc. action itself -- see
 * StepInteraction's doc comment in src/types/workflow.ts. For "approved"/
 * "sent" this stands in for "open the draft in the real approval queue";
 * wiring an actual link there is a later phase once this page talks to the
 * real API. Named export per AGENTS.md section 4.1.
 */
export function WorkflowCellPanel({ row, step, onClose }: WorkflowCellPanelProps): JSX.Element {
  const cell = row.steps[step.key];
  const isActionGated = step.interaction === "review";

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-slate-900/40 px-4">
      <div className="w-full max-w-md rounded-xl bg-white p-5 shadow-xl">
        <div className="mb-3 flex items-start justify-between">
          <div>
            <h3 className="text-sm font-semibold text-slate-800">
              {row.leadName} &ndash; {step.label}
            </h3>
            <p className="text-xs text-slate-400">{row.category}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        </div>

        <dl className="space-y-2 text-xs text-slate-600">
          <div className="flex justify-between gap-4">
            <dt className="text-slate-400">Status</dt>
            <dd className="font-medium text-slate-700">{STATUS_LABELS[cell.status]}</dd>
          </div>
          {cell.timestampLabel ? (
            <div className="flex justify-between gap-4">
              <dt className="text-slate-400">Last updated</dt>
              <dd className="text-slate-700">{cell.timestampLabel}</dd>
            </div>
          ) : null}
          {cell.detail ? (
            <div className="flex justify-between gap-4">
              <dt className="text-slate-400">Detail</dt>
              <dd className="text-right text-slate-700">{cell.detail}</dd>
            </div>
          ) : null}
        </dl>

        {isActionGated ? (
          <p className="mt-4 rounded-md bg-amber-50 px-3 py-2 text-[11px] text-amber-700">
            Approving and sending require full human review in the outreach queue --
            this panel is read-only and cannot approve or send on its own.
          </p>
        ) : null}

        <button
          type="button"
          onClick={onClose}
          className="mt-4 w-full rounded-md bg-slate-100 px-3 py-2 text-xs font-medium text-slate-600 hover:bg-slate-200"
        >
          Close
        </button>
      </div>
    </div>
  );
}
