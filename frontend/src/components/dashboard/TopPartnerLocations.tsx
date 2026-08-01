import { Globe2 } from "lucide-react";

import type { PartnerLocation } from "@/types/dashboard";

interface TopPartnerLocationsProps {
  locations: PartnerLocation[];
}

/**
 * Ranked list of partner locations by share of total partners.
 *
 * The reference design shows a shaded world map here. This phase renders a
 * ranked bar list instead of a literal choropleth -- a real map needs a
 * geo library (e.g. react-simple-maps) plus topojson data, which is a new
 * dependency this phase intentionally didn't add (design/mock-data pass
 * only, confirmed with the user). Swapping this out for a real map is a
 * good candidate for a later phase. Named export per AGENTS.md section 4.1.
 */
export function TopPartnerLocations({ locations }: TopPartnerLocationsProps): JSX.Element {
  return (
    <div>
      <p className="mb-3 flex items-center gap-2 text-xs font-medium text-slate-500">
        <Globe2 className="h-4 w-4" aria-hidden />
        Top Partner Locations
      </p>
      <div className="flex flex-col gap-2.5">
        {locations.map((location) => (
          <div key={location.id} className="flex items-center gap-3">
            <span className="w-28 shrink-0 truncate text-xs text-slate-600" title={location.place}>
              {location.place}
            </span>
            <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100">
              <div
                className="h-full rounded-full bg-gradient-to-r from-cyan-500 to-violet-600"
                style={{ width: `${location.shareOfTotalPct}%` }}
              />
            </div>
            <span className="w-8 shrink-0 text-right text-xs font-semibold text-slate-700">
              {location.partnerCount}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
