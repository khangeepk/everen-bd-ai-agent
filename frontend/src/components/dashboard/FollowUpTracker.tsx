import { CalendarClock } from "lucide-react";
import { useState } from "react";

import { PanelHeader } from "@/components/dashboard/PanelHeader";
import type { FollowUpRow, FollowUpStatus } from "@/types/dashboard";

interface FollowUpTrackerProps {
  rows: FollowUpRow[];
}

const STATUS_STYLES: Record<FollowUpStatus, string> = {
  Sent: "bg-emerald-50 text-emerald-700",
  Opened: "bg-sky-50 text-sky-700",
  Pending: "bg-amber-50 text-amber-700",
};

/**
 * "Automated Follow-up Tracker" table: per-contact AI follow-up suggestions
 * with a reviewed/actioned checkbox. The checkbox is local UI state only in
 * this phase -- wiring it to a real "mark reviewed" mutation belongs with
 * the outreach approval queue once this page talks to the live API. Named
 * export per AGENTS.md section 4.1.
 */
export function FollowUpTracker({ rows }: FollowUpTrackerProps): JSX.Element {
  const [reviewedIds, setReviewedIds] = useState<ReadonlySet<string>>(
    () => new Set(rows.filter((row) => row.reviewed).map((row) => row.id)),
  );

  function toggleReviewed(rowId: string): void {
    setReviewedIds((previous) => {
      const next = new Set(previous);
      if (next.has(rowId)) {
        next.delete(rowId);
      } else {
        next.add(rowId);
      }
      return next;
    });
  }

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <PanelHeader
        icon={<CalendarClock className="h-4 w-4 text-slate-500" aria-hidden />}
        title="Automated Follow-up Tracker"
      />

      <div className="overflow-x-auto">
        <table className="w-full min-w-[560px] text-left text-xs">
          <thead>
            <tr className="text-slate-400">
              <th className="pb-2 font-medium">Contact</th>
              <th className="pb-2 font-medium">Last Interaction</th>
              <th className="pb-2 font-medium">Follow-up</th>
              <th className="pb-2 font-medium">AI-generated Follow-up</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id} className="border-t border-slate-100">
                <td className="py-2.5 pr-2">
                  <div className="flex items-center gap-2">
                    <span
                      className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-slate-200 text-[10px] font-semibold text-slate-600"
                      aria-hidden
                    >
                      {row.contactName.charAt(0)}
                    </span>
                    <span className="font-medium text-slate-700">{row.contactName}</span>
                  </div>
                </td>
                <td className="py-2.5 pr-2 text-slate-500">{row.lastInteractionLabel}</td>
                <td className="py-2.5 pr-2">
                  <span
                    className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${STATUS_STYLES[row.status]}`}
                  >
                    {row.status}
                  </span>
                </td>
                <td className="py-2.5">
                  <label className="flex items-start gap-2">
                    <input
                      type="checkbox"
                      checked={reviewedIds.has(row.id)}
                      onChange={() => toggleReviewed(row.id)}
                      className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-brand-blue"
                    />
                    <span className="text-slate-600">{row.aiSuggestion}</span>
                  </label>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
