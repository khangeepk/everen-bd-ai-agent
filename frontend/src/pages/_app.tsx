import type { AppProps } from "next/app";

import "@/styles/globals.css";

/**
 * Next.js root app component. Only responsibility here is loading global
 * CSS (Tailwind) so it's available on every page. Named export per
 * AGENTS.md section 4.1 -- Next.js requires this file's export to be a
 * default export, which is the one sanctioned exception to that rule.
 */
// eslint-disable-next-line import/no-default-export -- Next.js requires a default export here.
export default function EverenBdAgentApp({ Component, pageProps }: AppProps): JSX.Element {
  return <Component {...pageProps} />;
}
