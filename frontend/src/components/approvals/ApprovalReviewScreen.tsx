import {
  Check,
  ChevronLeft,
  ChevronRight,
  Link2,
  MapPin,
  Pencil,
  SkipForward,
  Sparkles,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ComplianceNotice } from "@/components/common/ComplianceNotice";
import { LeadScoreBadge } from "@/components/common/LeadScoreBadge";
import { QuotaBar } from "@/components/approvals/QuotaBar";
import { auditConsequence } from "@/lib/plainLanguage";
import type { ApprovalDraft, ReviewDecision, SendQuota } from "@/types/approval";

interface ApprovalReviewScreenProps {
  drafts: readonly ApprovalDraft[];
  quota: SendQuota;
}

const SHORTCUTS: readonly { keys: string; label: string }[] = [
  { keys: "A", label: "Approve" },
  { keys: "R", label: "Reject" },
  { keys: "E", label: "Edit" },
  { keys: "S", label: "Skip" },
  { keys: "← →", label: "Prev / Next" },
  { keys: "X", label: "Select" },
  { keys: "B", label: "Bulk approve" },
];

/**
 * Keyboard-first, one-screen approval queue. Lead context, detected problems,
 * recommended service, and the draft are all visible together. Every claim in
 * the draft links back to the audit finding that produced it. Bulk approve
 * shows a confirmation summary before anything sends, and the daily send quota
 * is shown right here. Named export per AGENTS.md section 4.1.
 *
 * Interactions are local state only in this phase — wiring approve/reject to
 * the real gated endpoints (POST /outreach/drafts/{id}/approve) is a later
 * pass; the human-approval gate itself lives server-side (AGENTS.md section 8).
 */
export function ApprovalReviewScreen({ drafts, quota }: ApprovalReviewScreenProps): JSX.Element {
  const [index, setIndex] = useState<number>(0);
  const [decisions, setDecisions] = useState<Record<string, ReviewDecision>>({});
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState<boolean>(false);
  const [showBulkConfirm, setShowBulkConfirm] = useState<boolean>(false);

  const current = drafts[index];
  const blocked = current?.complianceState !== undefined;

  const decide = useCallback(
    (id: string, decision: ReviewDecision) => {
      setDecisions((d) => ({ ...d, [id]: decision }));
      setEditing(false);
      setIndex((i) => Math.min(i + 1, drafts.length - 1));
    },
    [drafts.length],
  );

  const toggleSelect = useCallback((id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  // Keyboard-first controls. Ignored while typing in the edit textarea.
  useEffect(() => {
    function onKey(e: KeyboardEvent): void {
      if (showBulkConfirm) {
        return;
      }
      const target = e.target as HTMLElement;
      if (target.tagName === "TEXTAREA" || target.tagName === "INPUT") {
        return;
      }
      if (!current) {
        return;
      }
      const key = e.key.toLowerCase();
      if (key === "a" && !blocked) {
        decide(current.id, "approve");
      } else if (key === "r") {
        decide(current.id, "reject");
      } else if (key === "e") {
        setEditing((v) => !v);
      } else if (key === "s") {
        setIndex((i) => Math.min(i + 1, drafts.length - 1));
      } else if (key === "x" && !blocked) {
        toggleSelect(current.id);
      } else if (key === "b" && selected.size > 0) {
        setShowBulkConfirm(true);
      } else if (e.key === "ArrowRight") {
        setIndex((i) => Math.min(i + 1, drafts.length - 1));
      } else if (e.key === "ArrowLeft") {
        setIndex((i) => Math.max(i - 1, 0));
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [current, blocked, decide, toggleSelect, drafts.length, selected.size, showBulkConfirm]);

  const reviewedCount = Object.keys(decisions).length;

  if (!current) {
    return <p className="text-sm text-slate-500">No drafts to review.</p>;
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Quota + progress */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_auto]">
        <QuotaBar quota={quota} pendingApprovals={selected.size} />
        <div className="flex items-center justify-between gap-4 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm shadow-sm">
          <span className="text-slate-500">
            Draft <span className="font-semibold text-slate-800">{index + 1}</span> of{" "}
            {drafts.length}
          </span>
          <span className="text-slate-500">
            <span className="font-semibold text-emerald-600">{reviewedCount}</span> reviewed
          </span>
          {selected.size > 0 ? (
            <button
              type="button"
              onClick={() => setShowBulkConfirm(true)}
              className="rounded-lg bg-brand-navy px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-900"
            >
              Bulk approve {selected.size}
            </button>
          ) : null}
        </div>
      </div>

      {/* One-screen review: context (left) + draft & provenance (right) */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* Left: lead context + problems + recommendation */}
        <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold text-slate-800">{current.leadName}</h2>
              <p className="mt-0.5 flex items-center gap-1 text-xs text-slate-500">
                <MapPin className="h-3 w-3" aria-hidden />
                {current.industry} · {current.location}
              </p>
            </div>
            {selected.has(current.id) ? (
              <span className="rounded-full bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-700">
                Selected
              </span>
            ) : null}
          </div>

          <div className="mt-3">
            <LeadScoreBadge
              score={current.score}
              reasons={current.scoreReasons}
              doNotContact={blocked}
            />
          </div>

          <p className="mt-4 text-xs font-semibold uppercase tracking-wide text-slate-400">
            What we found
          </p>
          <ul className="mt-1 space-y-1.5">
            {current.problems.map((p) => (
              <li key={p.category} className="flex gap-2 text-sm text-slate-600">
                <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-rose-400" />
                {auditConsequence(p.category, p.detail)}
              </li>
            ))}
          </ul>

          <div className="mt-4 rounded-lg bg-blue-50 p-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-blue-500">
              Recommended pitch
            </p>
            <p className="mt-0.5 text-sm font-medium text-slate-800">
              {current.recommendedService}
            </p>
          </div>

          {current.complianceState ? (
            <div className="mt-4">
              <ComplianceNotice state={current.complianceState} />
            </div>
          ) : null}
        </section>

        {/* Right: draft + claim provenance */}
        <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-slate-700">
              Draft · {current.channel}
            </h3>
            <button
              type="button"
              onClick={() => setEditing((v) => !v)}
              className="flex items-center gap-1 text-xs font-medium text-slate-500 hover:text-slate-700"
            >
              <Pencil className="h-3.5 w-3.5" aria-hidden />
              {editing ? "Done" : "Edit"}
            </button>
          </div>

          {current.subject ? (
            <p className="mt-2 text-sm">
              <span className="text-slate-400">Subject: </span>
              <span className="font-medium text-slate-800">{current.subject}</span>
            </p>
          ) : null}

          {editing ? (
            <textarea
              defaultValue={current.body}
              className="mt-2 h-40 w-full rounded-lg border border-slate-300 p-3 text-sm focus:border-brand-navy focus:outline-none"
            />
          ) : (
            <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-slate-700">
              {current.body}
            </p>
          )}

          {/* Provenance: why the draft says what it says */}
          <div className="mt-4 rounded-lg border border-slate-100 bg-slate-50 p-3">
            <p className="flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-slate-400">
              <Link2 className="h-3.5 w-3.5" aria-hidden />
              Why it says this
            </p>
            <ul className="mt-2 space-y-2">
              {current.claims.map((claim) => (
                <li key={claim.phrase} className="text-xs">
                  <span className="font-medium text-slate-700">&ldquo;{claim.phrase}&rdquo;</span>
                  <span className="mt-0.5 block text-slate-500">
                    ↳ {auditConsequence(claim.source)}
                    {claim.evidence ? ` — measured: ${claim.evidence}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </section>
      </div>

      {/* Keyboard action bar (always visible) */}
      <div className="sticky bottom-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-lg">
        <div className="flex flex-wrap items-center gap-2">
          <ActionButton
            onClick={() => decide(current.id, "approve")}
            disabled={blocked}
            tone="approve"
            icon={<Check className="h-4 w-4" aria-hidden />}
            label="Approve"
            shortcut="A"
          />
          <ActionButton
            onClick={() => decide(current.id, "reject")}
            tone="reject"
            icon={<X className="h-4 w-4" aria-hidden />}
            label="Reject"
            shortcut="R"
          />
          <ActionButton
            onClick={() => setEditing((v) => !v)}
            tone="neutral"
            icon={<Pencil className="h-4 w-4" aria-hidden />}
            label="Edit"
            shortcut="E"
          />
          <ActionButton
            onClick={() => setIndex((i) => Math.min(i + 1, drafts.length - 1))}
            tone="neutral"
            icon={<SkipForward className="h-4 w-4" aria-hidden />}
            label="Skip"
            shortcut="S"
          />
        </div>

        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => setIndex((i) => Math.max(i - 1, 0))}
            className="rounded-lg border border-slate-200 p-1.5 text-slate-500 hover:bg-slate-50"
            aria-label="Previous draft"
          >
            <ChevronLeft className="h-4 w-4" aria-hidden />
          </button>
          <button
            type="button"
            onClick={() => setIndex((i) => Math.min(i + 1, drafts.length - 1))}
            className="rounded-lg border border-slate-200 p-1.5 text-slate-500 hover:bg-slate-50"
            aria-label="Next draft"
          >
            <ChevronRight className="h-4 w-4" aria-hidden />
          </button>
        </div>
      </div>

      {/* On-screen shortcut legend */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-1 text-xs text-slate-400">
        {SHORTCUTS.map((s) => (
          <span key={s.keys} className="flex items-center gap-1">
            <kbd className="rounded border border-slate-300 bg-slate-50 px-1.5 py-0.5 font-mono text-[10px] text-slate-600">
              {s.keys}
            </kbd>
            {s.label}
          </span>
        ))}
      </div>

      {showBulkConfirm ? (
        <BulkConfirmModal
          count={selected.size}
          quota={quota}
          onCancel={() => setShowBulkConfirm(false)}
          onConfirm={() => {
            setDecisions((d) => {
              const next = { ...d };
              selected.forEach((id) => {
                next[id] = "approve";
              });
              return next;
            });
            setSelected(new Set());
            setShowBulkConfirm(false);
          }}
        />
      ) : null}
    </div>
  );
}

interface ActionButtonProps {
  onClick: () => void;
  tone: "approve" | "reject" | "neutral";
  icon: JSX.Element;
  label: string;
  shortcut: string;
  disabled?: boolean;
}

function ActionButton({
  onClick,
  tone,
  icon,
  label,
  shortcut,
  disabled = false,
}: ActionButtonProps): JSX.Element {
  const styles: Record<string, string> = {
    approve: "bg-emerald-600 text-white hover:bg-emerald-700",
    reject: "bg-white text-rose-600 border border-rose-200 hover:bg-rose-50",
    neutral: "bg-white text-slate-600 border border-slate-200 hover:bg-slate-50",
  };
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-40 ${styles[tone]}`}
    >
      {icon}
      {label}
      <kbd className="ml-0.5 rounded bg-black/10 px-1 py-0.5 font-mono text-[10px]">{shortcut}</kbd>
    </button>
  );
}

interface BulkConfirmModalProps {
  count: number;
  quota: SendQuota;
  onCancel: () => void;
  onConfirm: () => void;
}

/** Confirmation summary shown before any bulk approval commits. */
function BulkConfirmModal({ count, quota, onCancel, onConfirm }: BulkConfirmModalProps): JSX.Element {
  const remaining = Math.max(0, quota.dailyLimit - quota.sentToday);
  const sendNow = Math.min(count, remaining);
  const deferred = Math.max(0, count - remaining);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 px-4">
      <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
        <div className="flex items-center gap-2">
          <Sparkles className="h-5 w-5 text-blue-600" aria-hidden />
          <h2 className="text-base font-semibold text-slate-800">Approve {count} drafts?</h2>
        </div>
        <div className="mt-4 space-y-2 rounded-lg bg-slate-50 p-4 text-sm text-slate-600">
          <p className="flex justify-between">
            <span>Selected for approval</span>
            <span className="font-semibold text-slate-800">{count}</span>
          </p>
          <p className="flex justify-between">
            <span>Will send today</span>
            <span className="font-semibold text-emerald-600">{sendNow}</span>
          </p>
          {deferred > 0 ? (
            <p className="flex justify-between">
              <span>Sends tomorrow (daily limit)</span>
              <span className="font-semibold text-amber-600">{deferred}</span>
            </p>
          ) : null}
        </div>
        <p className="mt-3 text-xs text-slate-400">
          Nothing sends until you confirm. Approved drafts go out in order until the daily limit is
          reached.
        </p>
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className="rounded-lg bg-brand-navy px-4 py-2 text-sm font-semibold text-white hover:bg-blue-900"
          >
            Approve {count}
          </button>
        </div>
      </div>
    </div>
  );
}
