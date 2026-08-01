import { TrendingDown, TrendingUp } from "lucide-react";

import type { KpiMetric } from "@/types/dashboard";

interface KpiCardRowProps {
  metrics: KpiMetric[];
}

/**
 * Row of top-line KPI cards (Total Pipeline, Avg Deal Size, Win Rate, ...).
 * Named export per AGENTS.md section 4.1.
 */
export function KpiCardRow({ metrics }: KpiCardRowProps): JSX.Element {
  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
      {metrics.map((metric) => (
        <div key={metric.id} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <p className="text-xs font-medium text-slate-500">{metric.label}</p>
          <div className="mt-2 flex items-center gap-2">
            <span className="text-2xl font-semibold text-slate-900">{metric.value}</span>
            {metric.changeLabel ? (
              <span
                className={
                  metric.trend === "down"
                    ? "flex items-center gap-0.5 text-xs font-semibold text-rose-600"
                    : "flex items-center gap-0.5 text-xs font-semibold text-emerald-600"
                }
              >
                {metric.trend === "down" ? (
                  <TrendingDown className="h-3.5 w-3.5" aria-hidden />
                ) : (
                  <TrendingUp className="h-3.5 w-3.5" aria-hidden />
                )}
                {metric.changeLabel}
              </span>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  );
}
