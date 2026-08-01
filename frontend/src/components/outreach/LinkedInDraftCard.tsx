import { AlertTriangle, Check, Copy, Linkedin } from "lucide-react";
import { useState } from "react";

import type { LinkedInQueueItem } from "@/types/outreachQueue";

interface LinkedInDraftCardProps {
  item: LinkedInQueueItem;
}

/** Which text block was most recently copied, for the "Copied!" confirmation.
 * Null means nothing has been copied yet (or the confirmation already timed out). */
type CopiedField = "note" | "followup" | null;

/** How long the "Copied!" confirmation stays visible before reverting to "Copy". */
const COPIED_CONFIRMATION_MS = 1800;

/**
 * One pending-review LinkedIn draft: the connection-request note and
 * follow-up message, each with its own "Copy to clipboard" action for the
 * rep to paste into LinkedIn manually.
 *
 * Deliberately has no "send" or "approve" action -- this system has no
 * LinkedIn integration (see backend/app/services/outreach_policy.py's
 * module docstring) and copy-to-clipboard is the entire interaction this
 * card supports. Approving the draft (marking it reviewed) still happens
 * through the existing POST /outreach/drafts/{id}/approve endpoint, which
 * this first pass of the page does not wire a button for -- see the page's
 * "Known gaps" note. Named export per AGENTS.md section 4.1.
 */
export function LinkedInDraftCard({ item }: LinkedInDraftCardProps): JSX.Element {
  const [copiedField, setCopiedField] = useState<CopiedField>(null);

  async function copyToClipboard(field: CopiedField, text: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedField(field);
      setTimeout(() => setCopiedField((current) => (current === field ? null : current)), COPIED_CONFIRMATION_MS);
    } catch {
      // Clipboard access can be denied by the browser (e.g. no HTTPS, no
      // user-gesture context lost) -- fail quietly rather than throwing;
      // the rep can still select and copy the text manually from the page.
    }
  }

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Linkedin className="h-4 w-4 text-[#0a66c2]" aria-hidden />
            <h3 className="text-sm font-semibold text-slate-800">{item.leadName}</h3>
          </div>
          {item.contactName ? (
            <p className="mt-0.5 text-xs text-slate-400">{item.contactName}</p>
          ) : null}
        </div>
        <div className="flex flex-col items-end gap-1">
          <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700">
            Pending review
          </span>
          {item.linkedinUrl ? (
            <a
              href={item.linkedinUrl}
              target="_blank"
              rel="noreferrer"
              className="text-[11px] text-blue-600 hover:underline"
            >
              View profile
            </a>
          ) : null}
        </div>
      </div>

      <TextBlock
        label="Connection request note"
        helperText={`${item.connectionNote.length}/300 characters`}
        text={item.connectionNote}
        isCopied={copiedField === "note"}
        onCopy={() => void copyToClipboard("note", item.connectionNote)}
      />

      {item.followUpMessage ? (
        <div className="mt-4">
          <TextBlock
            label="Follow-up message (send after they accept)"
            text={item.followUpMessage}
            isCopied={copiedField === "followup"}
            onCopy={() => void copyToClipboard("followup", item.followUpMessage as string)}
          />
        </div>
      ) : null}

      {item.warnings.length > 0 ? (
        <div className="mt-4 flex gap-2 rounded-md bg-amber-50 px-3 py-2">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600" aria-hidden />
          <p className="text-[11px] leading-relaxed text-amber-700">{item.warnings[0]}</p>
        </div>
      ) : null}

      {item.usedFallback ? (
        <p className="mt-3 text-[11px] text-slate-400">
          Generated from a deterministic template (the LLM was unavailable when this was drafted).
        </p>
      ) : null}
    </div>
  );
}

interface TextBlockProps {
  label: string;
  helperText?: string;
  text: string;
  isCopied: boolean;
  onCopy: () => void;
}

/** One labeled block of draft text with its own copy button. Not exported --
 * only meaningful as part of LinkedInDraftCard's layout. */
function TextBlock({ label, helperText, text, isCopied, onCopy }: TextBlockProps): JSX.Element {
  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between gap-2">
        <span className="text-xs font-medium text-slate-500">{label}</span>
        {helperText ? <span className="text-[11px] text-slate-400">{helperText}</span> : null}
      </div>
      <div className="rounded-lg bg-slate-50 p-3">
        <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-700">{text}</p>
        <button
          type="button"
          onClick={onCopy}
          className={
            isCopied
              ? "mt-3 flex items-center gap-1.5 rounded-md bg-emerald-100 px-3 py-1.5 text-xs font-medium text-emerald-700"
              : "mt-3 flex items-center gap-1.5 rounded-md bg-white px-3 py-1.5 text-xs font-medium text-slate-600 shadow-sm ring-1 ring-slate-200 hover:bg-slate-100"
          }
        >
          {isCopied ? (
            <>
              <Check className="h-3.5 w-3.5" aria-hidden />
              Copied
            </>
          ) : (
            <>
              <Copy className="h-3.5 w-3.5" aria-hidden />
              Copy to clipboard
            </>
          )}
        </button>
      </div>
    </div>
  );
}
