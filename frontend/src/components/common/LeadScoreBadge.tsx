import { scoreReasonLine, scoreToLabel } from "@/lib/plainLanguage";

interface LeadScoreBadgeProps {
  /** Raw 0–1 score from the backend (never shown to the rep directly). */
  score: number;
  /** Short rep-friendly reason fragments, e.g. ["old website", "no mobile version"]. */
  reasons: readonly string[];
  /** Whether the lead is hard-blocked from contact. */
  doNotContact?: boolean;
}

const TONE_STYLES: Record<string, string> = {
  hot: "bg-rose-50 text-rose-700 border-rose-200",
  warm: "bg-amber-50 text-amber-700 border-amber-200",
  cold: "bg-slate-100 text-slate-600 border-slate-200",
  blocked: "bg-slate-200 text-slate-500 border-slate-300",
};

/**
 * Shows a lead's score as a label plus a one-line reason — never a bare number.
 * e.g. "Hot — old website, no mobile version, contact email found". Named
 * export per AGENTS.md section 4.1.
 */
export function LeadScoreBadge({
  score,
  reasons,
  doNotContact = false,
}: LeadScoreBadgeProps): JSX.Element {
  const { label, tone } = scoreToLabel(score, doNotContact);
  return (
    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
      <span
        className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold ${
          TONE_STYLES[tone] ?? TONE_STYLES.cold
        }`}
      >
        {label}
      </span>
      <span className="text-xs text-slate-500">{scoreReasonLine(reasons)}</span>
    </div>
  );
}
