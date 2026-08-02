import { AlertTriangle, RotateCw } from "lucide-react";

interface ErrorStateProps {
  /** Plain-language description of what went wrong. */
  message: string;
  /** Called when the user clicks Retry. */
  onRetry: () => void;
  /** Optional compact variant for inline use inside a panel. */
  compact?: boolean;
}

/**
 * Shown when an API call fails. Always gives a clear message AND a retry
 * action — never a blank panel, and never a silent fall back to sample data.
 * Named export per AGENTS.md section 4.1.
 */
export function ErrorState({ message, onRetry, compact = false }: ErrorStateProps): JSX.Element {
  return (
    <div
      className={`flex flex-col items-center gap-3 rounded-lg border border-rose-200 bg-rose-50 text-center ${
        compact ? "px-4 py-6" : "px-6 py-12"
      }`}
    >
      <span className="flex h-10 w-10 items-center justify-center rounded-full bg-rose-100">
        <AlertTriangle className="h-5 w-5 text-rose-600" aria-hidden />
      </span>
      <div>
        <p className="text-sm font-semibold text-slate-800">Couldn&rsquo;t load this</p>
        <p className="mt-0.5 max-w-sm text-xs text-slate-600">{message}</p>
      </div>
      <button
        type="button"
        onClick={onRetry}
        className="inline-flex items-center gap-1.5 rounded-lg bg-brand-navy px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-900"
      >
        <RotateCw className="h-4 w-4" aria-hidden />
        Try again
      </button>
    </div>
  );
}
