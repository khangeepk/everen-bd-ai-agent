import { type ReactNode, useId, useState } from "react";

interface TooltipProps {
  /** The tooltip text shown on hover/focus. */
  content: string;
  /** The element the tooltip describes (e.g. a stage chip). */
  children: ReactNode;
}

/**
 * Lightweight, accessible tooltip: shows on hover and keyboard focus, links to
 * its trigger via aria-describedby, and needs no external library. Named export
 * per AGENTS.md section 4.1.
 */
export function Tooltip({ content, children }: TooltipProps): JSX.Element {
  const [open, setOpen] = useState<boolean>(false);
  const id = useId();

  return (
    <span
      className="relative inline-flex"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
    >
      <span tabIndex={0} aria-describedby={open ? id : undefined} className="cursor-help">
        {children}
      </span>
      {open ? (
        <span
          role="tooltip"
          id={id}
          className="absolute bottom-full left-1/2 z-30 mb-2 w-56 -translate-x-1/2 rounded-lg bg-slate-800 px-3 py-2 text-xs leading-snug text-white shadow-lg"
        >
          {content}
        </span>
      ) : null}
    </span>
  );
}
