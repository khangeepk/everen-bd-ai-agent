import { ArrowRight, CheckCircle2, Sparkles } from "lucide-react";
import Link from "next/link";

import { deriveNextAction } from "@/lib/onboarding";
import type { PendingWorkCounts } from "@/types/onboarding";

interface NextActionBannerProps {
  /** Current pending-work counts used to pick the single top action. */
  counts: PendingWorkCounts;
}

/**
 * Persistent "What should I do now?" banner. Surfaces the single
 * highest-value pending task (see deriveNextAction) so the user always knows
 * the one thing worth doing next, instead of scanning every module. Named
 * export per AGENTS.md section 4.1.
 */
export function NextActionBanner({ counts }: NextActionBannerProps): JSX.Element {
  const action = deriveNextAction(counts);

  return (
    <section
      className={`flex flex-col gap-3 rounded-xl border p-4 sm:flex-row sm:items-center sm:justify-between ${
        action.allCaughtUp
          ? "border-emerald-200 bg-emerald-50"
          : "border-blue-200 bg-blue-50"
      }`}
      aria-label="What should I do now?"
    >
      <div className="flex items-start gap-3">
        <span
          className={`mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${
            action.allCaughtUp ? "bg-emerald-100" : "bg-blue-100"
          }`}
        >
          {action.allCaughtUp ? (
            <CheckCircle2 className="h-5 w-5 text-emerald-600" aria-hidden />
          ) : (
            <Sparkles className="h-5 w-5 text-blue-600" aria-hidden />
          )}
        </span>
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            What should I do now?
          </p>
          <p className="text-sm font-semibold text-slate-800">{action.headline}</p>
          <p className="text-sm text-slate-500">{action.detail}</p>
        </div>
      </div>
      <Link
        href={action.href}
        className={`inline-flex shrink-0 items-center gap-1.5 rounded-lg px-4 py-2 text-sm font-medium text-white transition-colors ${
          action.allCaughtUp
            ? "bg-emerald-600 hover:bg-emerald-700"
            : "bg-brand-navy hover:bg-blue-900"
        }`}
      >
        {action.ctaLabel}
        <ArrowRight className="h-4 w-4" aria-hidden />
      </Link>
    </section>
  );
}
