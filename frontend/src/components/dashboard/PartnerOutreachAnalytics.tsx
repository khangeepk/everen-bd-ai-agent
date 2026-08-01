import {
  Bar,
  BarChart,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
} from "recharts";

import type { OutreachVolumePoint, ResponseRateSlice } from "@/types/dashboard";

interface PartnerOutreachAnalyticsProps {
  volume: OutreachVolumePoint[];
  responseRate: ResponseRateSlice[];
}

const DONUT_COLORS: readonly string[] = ["#06b6d4", "#7c3aed"];

/**
 * Chart half of the "Partner Outreach Analytics" panel: monthly
 * send-volume bars plus an overall response-rate donut. Rendered inside
 * PartnerOutreachPanel, which supplies the card chrome and title. Named
 * export per AGENTS.md section 4.1.
 */
export function PartnerOutreachAnalytics({
  volume,
  responseRate,
}: PartnerOutreachAnalyticsProps): JSX.Element {
  const primaryResponseRate = responseRate[0]?.value ?? 0;

  return (
    <div className="grid grid-cols-1 gap-6 sm:grid-cols-2">
        <div>
          <p className="mb-2 text-xs font-medium text-slate-500">Partner Outreach Volume</p>
          <div className="h-40">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={volume} margin={{ top: 4, right: 4, bottom: 0, left: -20 }}>
                <XAxis
                  dataKey="month"
                  tickLine={false}
                  axisLine={false}
                  tick={{ fontSize: 11, fill: "#64748b" }}
                />
                <Tooltip cursor={{ fill: "#f1f5f9" }} />
                <Bar dataKey="emails" fill="#06b6d4" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div>
          <p className="mb-2 text-xs font-medium text-slate-500">Response Rates</p>
          <div className="relative h-40">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={responseRate}
                  dataKey="value"
                  nameKey="label"
                  innerRadius={45}
                  outerRadius={65}
                  paddingAngle={2}
                  stroke="none"
                >
                  {responseRate.map((slice, index) => (
                    <Cell key={slice.label} fill={DONUT_COLORS[index % DONUT_COLORS.length]} />
                  ))}
                </Pie>
              </PieChart>
            </ResponsiveContainer>
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
              <span className="text-xl font-semibold text-slate-800">{primaryResponseRate}%</span>
            </div>
          </div>
          <div className="mt-2 flex items-center justify-center gap-4 text-xs text-slate-500">
            {responseRate.map((slice, index) => (
              <span key={slice.label} className="flex items-center gap-1.5">
                <span
                  className="h-2.5 w-2.5 rounded-full"
                  style={{ backgroundColor: DONUT_COLORS[index % DONUT_COLORS.length] }}
                  aria-hidden
                />
                {slice.label}
              </span>
            ))}
          </div>
        </div>
      </div>
  );
}

