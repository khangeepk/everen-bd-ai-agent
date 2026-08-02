import type { AppProps } from "next/app";

import { OnboardingGate } from "@/components/onboarding/OnboardingGate";
import "@/styles/globals.css";

/**
 * Next.js root app component. Loads global CSS (Tailwind) and mounts the
 * first-run onboarding gate above every page (it self-hides for returning
 * users). Named export per AGENTS.md section 4.1 -- Next.js requires this
 * file's export to be a default export, the one sanctioned exception.
 */
// eslint-disable-next-line import/no-default-export -- Next.js requires a default export here.
export default function EverenBdAgentApp({ Component, pageProps }: AppProps): JSX.Element {
  return (
    <>
      <Component {...pageProps} />
      <OnboardingGate />
    </>
  );
}
