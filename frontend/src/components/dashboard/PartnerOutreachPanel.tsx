import { BarChart2 } from "lucide-react";

import { PanelHeader } from "@/components/dashboard/PanelHeader";
import { PartnerOutreachAnalytics } from "@/components/dashboard/PartnerOutreachAnalytics";
import { TopPartnerLocations } from "@/components/dashboard/TopPartnerLocations";
import type { OutreachVolumePoint, PartnerLocation, ResponseRateSlice } from "@/types/dashboard";

interface PartnerOutreachPanelProps {
  volume: OutreachVolumePoint[];
  responseRate: ResponseRateSlice[];
  locations: PartnerLocation[];
}

/**
 * "Partner Outreach Analytics" card: charts up top, ranked partner
 * locations below, sharing one panel header/border. Named export per
 * AGENTS.md section 4.1.
 */
export function PartnerOutreachPanel({
  volume,
  responseRate,
  locations,
}: PartnerOutreachPanelProps): JSX.Element {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <PanelHeader
        icon={<BarChart2 className="h-4 w-4 text-slate-500" aria-hidden />}
        title="Partner Outreach Analytics"
      />
      <PartnerOutreachAnalytics volume={volume} responseRate={responseRate} />
      <div className="my-4 border-t border-slate-100" />
      <TopPartnerLocations locations={locations} />
    </section>
  );
}
