import type { ChatResults } from "@/types/chat";

interface ChatResultsTableProps {
  results: ChatResults;
}

/**
 * Shared result table for the chat panel -- a leads_list outcome and a
 * places_search outcome both render through this one component ("return
 * results in the same table view", per this feature's scope), just with
 * different columns for the two row shapes. Named export per AGENTS.md
 * section 4.1.
 */
export function ChatResultsTable({ results }: ChatResultsTableProps): JSX.Element {
  if (results.rows.length === 0) {
    return <p className="mt-2 text-xs text-slate-400">No matches.</p>;
  }

  if (results.kind === "leads") {
    return (
      <div className="mt-2 overflow-x-auto rounded-lg border border-slate-100">
        <table className="w-full min-w-[520px] text-left text-xs">
          <thead>
            <tr className="bg-slate-50 text-slate-400">
              <th className="px-3 py-2 font-medium">Lead</th>
              <th className="px-3 py-2 font-medium">Category</th>
              <th className="px-3 py-2 font-medium">Status</th>
              <th className="px-3 py-2 font-medium">Confidence</th>
              <th className="px-3 py-2 font-medium">Score</th>
              <th className="px-3 py-2 font-medium">Contact</th>
            </tr>
          </thead>
          <tbody>
            {results.rows.map((row) => (
              <tr key={row.id} className="border-t border-slate-100">
                <td className="px-3 py-2 font-medium text-slate-700">{row.name}</td>
                <td className="px-3 py-2 text-slate-500">{row.category ?? "--"}</td>
                <td className="px-3 py-2 text-slate-500">{row.status}</td>
                <td className="px-3 py-2 text-slate-500">{row.confidencePercent}%</td>
                <td className="px-3 py-2 text-slate-500">
                  {row.scorePercent === null ? "Not scored" : `${row.scorePercent}%`}
                </td>
                <td className="px-3 py-2 text-slate-500">{row.contactEmail ?? "--"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="mt-2 overflow-x-auto rounded-lg border border-slate-100">
      <table className="w-full min-w-[520px] text-left text-xs">
        <thead>
          <tr className="bg-slate-50 text-slate-400">
            <th className="px-3 py-2 font-medium">Business</th>
            <th className="px-3 py-2 font-medium">Address</th>
            <th className="px-3 py-2 font-medium">Website</th>
            <th className="px-3 py-2 font-medium">Phone</th>
          </tr>
        </thead>
        <tbody>
          {results.rows.map((row) => (
            <tr key={row.id} className="border-t border-slate-100">
              <td className="px-3 py-2 font-medium text-slate-700">{row.name}</td>
              <td className="px-3 py-2 text-slate-500">{row.address ?? "--"}</td>
              <td className="px-3 py-2 text-slate-500">
                {row.website ? (
                  <a
                    href={row.website}
                    target="_blank"
                    rel="noreferrer"
                    className="text-brand-blue hover:underline"
                  >
                    {row.website}
                  </a>
                ) : (
                  <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700">
                    No website
                  </span>
                )}
              </td>
              <td className="px-3 py-2 text-slate-500">{row.phone ?? "--"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
