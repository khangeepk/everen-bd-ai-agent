interface ComingSoonProps {
  title: string;
  description: string;
}

/**
 * Placeholder body for a nav page that has a link but no built page yet, so
 * the top nav is fully clickable without any route 404ing. Named export per
 * AGENTS.md section 4.1.
 */
export function ComingSoon({ title, description }: ComingSoonProps): JSX.Element {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center rounded-xl border border-dashed border-slate-300 bg-white/60 px-6 py-20 text-center">
      <h1 className="text-xl font-semibold text-slate-800">{title}</h1>
      <p className="mt-2 max-w-md text-sm text-slate-500">{description}</p>
      <span className="mt-6 rounded-full bg-blue-50 px-3 py-1 text-xs font-medium text-blue-700">
        Coming in a later phase
      </span>
    </div>
  );
}
