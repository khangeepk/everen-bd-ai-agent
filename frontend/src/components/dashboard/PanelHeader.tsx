import { MoreHorizontal } from "lucide-react";
import type { ReactNode } from "react";

interface PanelHeaderProps {
  icon?: ReactNode;
  title: string;
}

/**
 * Shared card header used by every dashboard panel: an optional leading
 * icon, a title, and a trailing overflow ("...") affordance. Named export
 * per AGENTS.md section 4.1.
 */
export function PanelHeader({ icon, title }: PanelHeaderProps): JSX.Element {
  return (
    <div className="mb-4 flex items-center justify-between">
      <div className="flex items-center gap-2">
        {icon}
        <h2 className="text-sm font-semibold text-slate-800">{title}</h2>
      </div>
      <button
        type="button"
        aria-label={`More options for ${title}`}
        className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
      >
        <MoreHorizontal className="h-4 w-4" aria-hidden />
      </button>
    </div>
  );
}
