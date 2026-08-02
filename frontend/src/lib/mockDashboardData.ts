/**
 * Sample data for the B2B Deal Flow dashboard.
 *
 * Deliberately static and in-memory -- this phase is a design/layout pass
 * only (confirmed with the user before building). No network calls happen
 * here. Swapping this module out for a real fetch against
 * NEXT_PUBLIC_API_BASE_URL is the next phase's job.
 */

import type {
  FollowUpRow,
  FunnelColumn,
  KpiMetric,
  OutreachVolumePoint,
  PartnerLocation,
  RecentWorkflow,
  ResponseRateSlice,
  WorkflowCanvasEdge,
  WorkflowCanvasNode,
  WorkflowLibraryItem,
} from "@/types/dashboard";

export const kpiMetrics: KpiMetric[] = [
  { id: "pipeline", label: "Total Pipeline", value: "$4.15M", changeLabel: "+12%", trend: "up" },
  { id: "avg-deal", label: "Avg Deal Size", value: "$12,500" },
  { id: "win-rate", label: "Win Rate", value: "38%" },
  { id: "leads", label: "B2B Leads Generated", value: "212" },
  { id: "partners", label: "Partner Sign-ups", value: "45" },
];

export const funnelColumns: FunnelColumn[] = [
  {
    id: "prospecting",
    title: "Prospecting",
    deals: [
      {
        id: "d1",
        accountName: "Zenith Corp",
        dealValueLabel: "$4.15M",
        score: 0.82,
        scoreReasons: ["old website", "no mobile version", "contact email found"],
      },
      {
        id: "d2",
        accountName: "Global Tech",
        dealValueLabel: "$4.15M",
        score: 0.61,
        scoreReasons: ["slow site", "weak social presence"],
      },
    ],
  },
  {
    id: "qualification",
    title: "Qualification",
    deals: [
      {
        id: "d3",
        accountName: "Acme Solutions",
        dealValueLabel: "$12,500",
        score: 0.34,
        scoreReasons: ["modern site", "no email found"],
      },
      {
        id: "d4",
        accountName: "Riverside Dental",
        dealValueLabel: "$12,500",
        score: 0.9,
        scoreReasons: ["no website"],
        complianceState: "unsubscribed",
      },
    ],
  },
  {
    id: "proposal",
    title: "Proposal",
    deals: [
      { id: "d5", accountName: "Global Tech", dealValueLabel: "$12,500" },
      { id: "d6", accountName: "Zenith Corp", dealValueLabel: "$17,500" },
    ],
  },
  {
    id: "negotiation",
    title: "Negotiation",
    deals: [
      { id: "d7", accountName: "Global Tech", dealValueLabel: "$3,900" },
      { id: "d8", accountName: "Acme Solutions", dealValueLabel: "$12,500" },
    ],
  },
  {
    id: "closing",
    title: "Closing",
    deals: [{ id: "d9", accountName: "Sabus Tech", dealValueLabel: "$17,500" }],
  },
  {
    id: "partner-onboarding",
    title: "Partner Onboarding",
    deals: [{ id: "d10", accountName: "Partner Ondars", dealValueLabel: "$12,500" }],
  },
];

export const outreachVolume: OutreachVolumePoint[] = [
  { month: "Jan", emails: 110, calls: 40 },
  { month: "Feb", emails: 150, calls: 55 },
  { month: "Mar", emails: 185, calls: 60 },
  { month: "Apr", emails: 205, calls: 70 },
  { month: "May", emails: 130, calls: 50 },
];

export const responseRateSlices: ResponseRateSlice[] = [
  { label: "Cyan", value: 55, colorClassName: "fill-cyan-500" },
  { label: "Purple", value: 45, colorClassName: "fill-violet-600" },
];

export const partnerLocations: PartnerLocation[] = [
  { id: "us", place: "United States", partnerCount: 22, shareOfTotalPct: 49 },
  { id: "uk", place: "United Kingdom", partnerCount: 8, shareOfTotalPct: 18 },
  { id: "ca", place: "Canada", partnerCount: 6, shareOfTotalPct: 13 },
  { id: "de", place: "Germany", partnerCount: 5, shareOfTotalPct: 11 },
  { id: "au", place: "Australia", partnerCount: 4, shareOfTotalPct: 9 },
];

export const followUpRows: FollowUpRow[] = [
  {
    id: "f1",
    contactName: "Zenith Corp",
    lastInteractionLabel: "Dec 12, 2024",
    status: "Sent",
    aiSuggestion: "AI-generated follow-up suggestions to the new contact...",
    reviewed: false,
  },
  {
    id: "f2",
    contactName: "Kane Grads",
    lastInteractionLabel: "Nov 12, 2024",
    status: "Opened",
    aiSuggestion: "AI-generated follow-up: get suggestions for contacts?",
    reviewed: false,
  },
  {
    id: "f3",
    contactName: "Acme Ondars",
    lastInteractionLabel: "Nov 16, 2024",
    status: "Opened",
    aiSuggestion: "AI-generated follow-up should we contact them?",
    reviewed: false,
  },
  {
    id: "f4",
    contactName: "Acme Solutions",
    lastInteractionLabel: "Nov 18, 2024",
    status: "Pending",
    aiSuggestion: "AI-generated follow-up should we need contacts?",
    reviewed: true,
  },
  {
    id: "f5",
    contactName: "Slabai Rewtors",
    lastInteractionLabel: "Nov 12, 2024",
    status: "Pending",
    aiSuggestion: "AI-generated follow-up: get suggestions in your contacts...",
    reviewed: false,
  },
];

export const workflowLibrary: WorkflowLibraryItem[] = [
  { id: "lib-new-lead", label: "New Lead", kind: "trigger" },
  { id: "lib-action", label: "Action", kind: "action" },
  { id: "lib-trigger", label: "Trigger", kind: "trigger" },
  { id: "lib-condition", label: "Condition", kind: "condition" },
];

export const workflowCanvasNodes: WorkflowCanvasNode[] = [
  { id: "n1", kind: "trigger", title: "Trigger", subtitle: "New Lead", x: 8, y: 20 },
  { id: "n2", kind: "action", title: "Action", subtitle: "Enrich Contact", x: 55, y: 12 },
  { id: "n3", kind: "condition", title: "Condition", subtitle: "Score > 80", x: 8, y: 68 },
  { id: "n4", kind: "action", title: "Action", subtitle: "Assign to Agent", x: 55, y: 74 },
];

export const workflowCanvasEdges: WorkflowCanvasEdge[] = [
  { id: "e1", fromNodeId: "n1", toNodeId: "n2" },
  { id: "e2", fromNodeId: "n3", toNodeId: "n4" },
];

export const recentWorkflows: RecentWorkflow[] = [
  { id: "r1", label: "New Lead" },
  { id: "r2", label: "Enrich Contact" },
  { id: "r3", label: "Global Tech" },
  { id: "r4", label: "Assign to Agent" },
  { id: "r5", label: "Assign to Agent" },
  { id: "r6", label: "Partner Workflows" },
];
