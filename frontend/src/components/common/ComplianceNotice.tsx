import { ShieldAlert } from "lucide-react";

import { type ComplianceState, complianceSentence } from "@/lib/plainLanguage";

interface ComplianceNoticeProps {
  /** Why the lead is blocked. */
  state: ComplianceState;
}

/**
 * Renders a compliance block as a plain sentence explaining why the lead can't
 * be contacted (not a raw flag like "suppressed" or "DNC"). Named export per
 * AGENTS.md section 4.1.
 */
export function ComplianceNotice({ state }: ComplianceNoticeProps): JSX.Element {
  return (
    <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
      <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" aria-hidden />
      <p className="text-xs leading-snug text-amber-800">{complianceSentence(state)}</p>
    </div>
  );
}
