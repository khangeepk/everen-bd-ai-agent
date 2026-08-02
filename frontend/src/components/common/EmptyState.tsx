import type { LucideIcon } from "lucide-react";
import Link from "next/link";

interface EmptyStateProps {
  /** Icon shown above the message. */
  icon: LucideIcon;
  /** Short headline — what this area is, not "No data found". */
  title: string;
  /** Plain-language sentence telling the user the exact next thing to do. */
  guidance: string;
  /** Optional call-to-action button. */
  cta?: { label: string; href: string };
}

/**
 * Friendly empty state for a built page that currently has no data. Instead of
 * a dead-end "No data found", it names the area and tells the user, in plain
 * language, the single next action that will populate it. Named export per
 * AGENTS.md section 4.1.
 */
export function EmptyState({ icon: Icon, title, guidance, cta }: EmptyStateProps): JSX.Element {
  return (
    <div className="flex min-h-[40vh] flex-col items-center justify-center rounded-xl border border-dashed border-slate-300 bg-white/70 px-6 py-16 text-center">
      <span className="flex h-12 w-12 items-center justify-center rounded-full bg-blue-50">
        <Icon className="h-6 w-6 text-blue-600" aria-hidden />
      </span>
      <h2 className="mt-4 text-base font-semibold text-slate-800">{title}</h2>
      <p className="mt-1 max-w-sm text-sm text-slate-500">{guidance}</p>
      {cta ? (
        <Link
          href={cta.href}
          className="mt-5 inline-flex items-center rounded-lg bg-brand-navy px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-900"
        >
          {cta.label}
        </Link>
      ) : null}
    </div>
  );
}
