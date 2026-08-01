import Head from "next/head";

import { AppShell } from "@/components/layout/AppShell";
import { ComingSoon } from "@/components/layout/ComingSoon";

// eslint-disable-next-line import/no-default-export -- Next.js requires a default export here.
export default function AnalyticsPage(): JSX.Element {
  return (
    <AppShell>
      <Head>
        <title>Everen BD Agent -- Analytics</title>
      </Head>
      <ComingSoon
        title="Analytics"
        description="This will surface the existing Phase 7 analytics API (A/B results, open rates, funnel metrics) in a later phase."
      />
    </AppShell>
  );
}
