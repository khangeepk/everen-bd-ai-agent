import { Check, ClipboardCheck, MapPin, Search, Send } from "lucide-react";
import { type ReactNode, useState } from "react";

import { markOnboardingComplete } from "@/lib/onboarding";
import type { OnboardingSelection, OnboardingStepId } from "@/types/onboarding";

interface OnboardingWizardProps {
  /** Called after the user finishes or skips, so the host can hide the wizard. */
  onDone: () => void;
}

interface StepMeta {
  id: OnboardingStepId;
  label: string;
}

const STEPS: readonly StepMeta[] = [
  { id: "services", label: "Confirm services" },
  { id: "location", label: "Pick a target" },
  { id: "review", label: "Review findings" },
  { id: "approve", label: "Approve a draft" },
];

/** Representative services the agent pitches — shown for confirmation in step 1. */
const SAMPLE_SERVICES = [
  "Website redesign & performance",
  "Local SEO & Google Business Profile",
  "Reputation / review management",
  "Social media presence setup",
];

/**
 * Guided 4-step first-run wizard shown over the app on first login. Each step
 * renders ONLY its own content — everything else is hidden — so a first-time
 * user is never shown the full dashboard at once. State is local; finishing
 * persists a "completed" flag (see lib/onboarding.ts). Named export per
 * AGENTS.md section 4.1.
 */
export function OnboardingWizard({ onDone }: OnboardingWizardProps): JSX.Element {
  const [stepIndex, setStepIndex] = useState<number>(0);
  const [selection, setSelection] = useState<OnboardingSelection>({
    servicesConfirmed: false,
    postalCode: "",
    industry: "",
  });

  const step = STEPS[stepIndex];
  const isLast = stepIndex === STEPS.length - 1;
  const canAdvance = stepCanAdvance(step?.id, selection);

  function finish(): void {
    markOnboardingComplete(selection);
    onDone();
  }

  function next(): void {
    if (isLast) {
      finish();
      return;
    }
    setStepIndex((i) => Math.min(i + 1, STEPS.length - 1));
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 px-4">
      <div className="w-full max-w-lg overflow-hidden rounded-2xl bg-white shadow-xl">
        {/* Progress header */}
        <div className="border-b border-slate-100 px-6 pb-4 pt-5">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wide text-blue-600">
              Getting started · Step {stepIndex + 1} of {STEPS.length}
            </span>
            <button
              type="button"
              onClick={finish}
              className="text-xs font-medium text-slate-400 hover:text-slate-600"
            >
              Skip for now
            </button>
          </div>
          <div className="mt-3 flex gap-1.5">
            {STEPS.map((s, i) => (
              <span
                key={s.id}
                className={`h-1.5 flex-1 rounded-full ${
                  i <= stepIndex ? "bg-brand-navy" : "bg-slate-200"
                }`}
              />
            ))}
          </div>
        </div>

        {/* Only the current step's body renders */}
        <div className="px-6 py-6">
          {step?.id === "services" ? (
            <StepShell
              icon={<ClipboardCheck className="h-5 w-5 text-blue-600" aria-hidden />}
              title="Confirm what you sell"
              subtitle="The agent pitches these services when it finds a good-fit business. Do they look right?"
            >
              <ul className="space-y-2">
                {SAMPLE_SERVICES.map((svc) => (
                  <li
                    key={svc}
                    className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700"
                  >
                    <Check className="h-4 w-4 text-emerald-500" aria-hidden />
                    {svc}
                  </li>
                ))}
              </ul>
              <label className="mt-4 flex items-center gap-2 text-sm text-slate-700">
                <input
                  type="checkbox"
                  checked={selection.servicesConfirmed}
                  onChange={(e) =>
                    setSelection((s) => ({ ...s, servicesConfirmed: e.target.checked }))
                  }
                  className="h-4 w-4 rounded border-slate-300"
                />
                Yes, these are the services I offer.
              </label>
            </StepShell>
          ) : null}

          {step?.id === "location" ? (
            <StepShell
              icon={<MapPin className="h-5 w-5 text-blue-600" aria-hidden />}
              title="Pick your first target"
              subtitle="Choose one area and one industry. The agent will look for businesses there with a weak online presence."
            >
              <div className="space-y-3">
                <label className="block text-sm font-medium text-slate-700">
                  Postal / ZIP code
                  <input
                    type="text"
                    inputMode="numeric"
                    placeholder="e.g. 78745"
                    value={selection.postalCode}
                    onChange={(e) =>
                      setSelection((s) => ({ ...s, postalCode: e.target.value }))
                    }
                    className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-brand-navy focus:outline-none"
                  />
                </label>
                <label className="block text-sm font-medium text-slate-700">
                  Industry
                  <input
                    type="text"
                    placeholder="e.g. auto repair shops"
                    value={selection.industry}
                    onChange={(e) =>
                      setSelection((s) => ({ ...s, industry: e.target.value }))
                    }
                    className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-brand-navy focus:outline-none"
                  />
                </label>
              </div>
            </StepShell>
          ) : null}

          {step?.id === "review" ? (
            <StepShell
              icon={<Search className="h-5 w-5 text-blue-600" aria-hidden />}
              title="Here's what happens next"
              subtitle={`When you run discovery, the agent searches ${
                selection.industry || "your industry"
              } near ${
                selection.postalCode || "your ZIP"
              }, audits each website, and scores the leads for you.`}
            >
              <div className="space-y-2 rounded-lg bg-slate-50 p-4 text-sm text-slate-600">
                <p className="flex items-center gap-2">
                  <span className="font-semibold text-slate-800">1.</span> Finds businesses
                  and their contact details
                </p>
                <p className="flex items-center gap-2">
                  <span className="font-semibold text-slate-800">2.</span> Audits each site
                  (speed, mobile, SSL, social)
                </p>
                <p className="flex items-center gap-2">
                  <span className="font-semibold text-slate-800">3.</span> Scores each lead
                  Hot / Warm / Cold
                </p>
                <p className="flex items-center gap-2">
                  <span className="font-semibold text-slate-800">4.</span> Drafts a tailored
                  message — for you to approve
                </p>
              </div>
            </StepShell>
          ) : null}

          {step?.id === "approve" ? (
            <StepShell
              icon={<Send className="h-5 w-5 text-blue-600" aria-hidden />}
              title="You always approve before anything sends"
              subtitle="Every draft waits for your review. Nothing goes out automatically — you're in control."
            >
              <div className="rounded-lg border border-slate-200 p-4">
                <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
                  Example draft
                </p>
                <p className="mt-2 text-sm text-slate-700">
                  &ldquo;Hi — I noticed your site loads slowly on mobile and isn&rsquo;t
                  showing up in local search. We help {selection.industry || "businesses"}{" "}
                  like yours fix exactly that…&rdquo;
                </p>
                <div className="mt-3 flex gap-2">
                  <span className="rounded-md bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-700">
                    Approve
                  </span>
                  <span className="rounded-md bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-600">
                    Edit
                  </span>
                  <span className="rounded-md bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-600">
                    Reject
                  </span>
                </div>
              </div>
            </StepShell>
          ) : null}
        </div>

        {/* Footer nav */}
        <div className="flex items-center justify-between border-t border-slate-100 px-6 py-4">
          <button
            type="button"
            onClick={() => setStepIndex((i) => Math.max(i - 1, 0))}
            disabled={stepIndex === 0}
            className="text-sm font-medium text-slate-500 disabled:opacity-40"
          >
            Back
          </button>
          <button
            type="button"
            onClick={next}
            disabled={!canAdvance}
            className="rounded-lg bg-brand-navy px-5 py-2 text-sm font-semibold text-white transition-colors hover:bg-blue-900 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isLast ? "Finish & go to dashboard" : "Next"}
          </button>
        </div>
      </div>
    </div>
  );
}

interface StepShellProps {
  icon: JSX.Element;
  title: string;
  subtitle: string;
  children: ReactNode;
}

/** Common layout for a single step's header + body. */
function StepShell({ icon, title, subtitle, children }: StepShellProps): JSX.Element {
  return (
    <div>
      <div className="flex items-center gap-2">
        <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-blue-50">
          {icon}
        </span>
        <h2 className="text-base font-semibold text-slate-800">{title}</h2>
      </div>
      <p className="mt-2 text-sm text-slate-500">{subtitle}</p>
      <div className="mt-4">{children}</div>
    </div>
  );
}

/** Per-step gate: which steps require input before "Next" enables. */
function stepCanAdvance(
  id: OnboardingStepId | undefined,
  selection: OnboardingSelection,
): boolean {
  if (id === "services") {
    return selection.servicesConfirmed;
  }
  if (id === "location") {
    return selection.postalCode.trim().length > 0 && selection.industry.trim().length > 0;
  }
  return true;
}
