import { Gauge } from "lucide-react";

import type { SendQuota } from "@/types/approval";

interface QuotaBarProps {
  quota: SendQuota;
  /** How many drafts are currently selected for approval (to preview impact). */
  pendingApprovals?: number;
}

/**
 * Shows today's send quota and remaining daily limit right on the review
 * screen — not buried in settings — plus a live preview of how many of the
 * currently-selected drafts fit within what's left. Named export per AGENTS.md
 * section 4.1.
 */
export function QuotaBar({ quota, pendingApprovals = 0 }: QuotaBarProps): JSX.Element {
  const remaining = Math.max(0, quota.dailyLimit - quota.sentToday);
  const usedPct = Math.min(100, Math.round((quota.sentToday / quota.dailyLimit) * 100));
  const wouldExceed = pendingApprovals > remaining;

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Gauge className="h-4 w-4 text-slate-500" aria-hidden />
          <span className="text-sm font-semibold text-slate-700">Today&rsquo;s send limit</span>
        </div>
        <span className="text-sm text-slate-600">
          <span className="font-semibold text-slate-800">{remaining}</span> of {quota.dailyLimit}{" "}
          left
        </span>
      </div>
      <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-slate-100">
        <div
          className={`h-full rounded-full ${usedPct >= 90 ? "bg-rose-500" : "bg-brand-navy"}`}
          style={{ width: `${usedPct}%` }}
        />
      </div>
      <p className="mt-2 text-xs text-slate-500">
        {quota.sentToday} sent today.{" "}
        {pendingApprovals > 0 ? (
          wouldExceed ? (
            <span className="font-medium text-rose-600">
              You&rsquo;ve selected {pendingApprovals} — {pendingApprovals - remaining} won&rsquo;t
              send until tomorrow.
            </span>
          ) : (
            <span className="text-slate-600">
              {pendingApprovals} selected will all fit within today&rsquo;s limit.
            </span>
          )
        ) : (
          "Approved drafts send in order until the daily limit is reached."
        )}
      </p>
    </div>
  );
}
