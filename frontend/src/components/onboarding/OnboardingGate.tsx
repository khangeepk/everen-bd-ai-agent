import { useEffect, useState } from "react";

import { OnboardingWizard } from "@/components/onboarding/OnboardingWizard";
import { hasCompletedOnboarding } from "@/lib/onboarding";

/**
 * Mounts the onboarding wizard over the app on first login only.
 *
 * SSR-safe: renders nothing on the server and during the first client paint,
 * then checks ``localStorage`` in an effect and shows the wizard if onboarding
 * hasn't been completed. This avoids both a hydration mismatch and a flash of
 * the wizard for returning users. Named export per AGENTS.md section 4.1.
 */
export function OnboardingGate(): JSX.Element | null {
  const [show, setShow] = useState<boolean>(false);

  useEffect(() => {
    if (!hasCompletedOnboarding()) {
      setShow(true);
    }
  }, []);

  if (!show) {
    return null;
  }
  return <OnboardingWizard onDone={() => setShow(false)} />;
}
