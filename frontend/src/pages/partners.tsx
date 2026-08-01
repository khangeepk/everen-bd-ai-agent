import Head from "next/head";

import { AppShell } from "@/components/layout/AppShell";
import { ComingSoon } from "@/components/layout/ComingSoon";

// eslint-disable-next-line import/no-default-export -- Next.js requires a default export here.
export default function PartnersPage(): JSX.Element {
  return (
    <AppShell>
      <Head>
        <title>Everen BD Agent -- Partners</title>
      </Head>
      <ComingSoon
        title="Partners"
        description="Referral/partner account management lands in a later phase."
      />
    </AppShell>
  );
}
