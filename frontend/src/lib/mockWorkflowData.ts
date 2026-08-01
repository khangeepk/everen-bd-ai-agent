/**
 * Sample data for the pipeline workflow grid (spreadsheet view).
 *
 * Deliberately static and in-memory -- this phase is a design/layout pass
 * only (confirmed with the user before building), matching every other
 * dashboard data module in this codebase (see mockDashboardData.ts). No
 * network calls happen here. Swapping this out for a real fetch against the
 * backend's per-lead status is a later phase's job.
 */

import type { PipelineStepMeta, WorkflowLeadRow } from "@/types/workflow";

/**
 * The 8 fixed pipeline columns, in order. Interaction determines what
 * clicking a cell in that column does -- see StepInteraction's doc comment
 * in src/types/workflow.ts for why "approved"/"sent" only ever open a
 * review panel rather than firing the action directly.
 */
export const pipelineSteps: PipelineStepMeta[] = [
  { key: "discovered", label: "Discovered", interaction: "detail" },
  { key: "enriched", label: "Enriched", interaction: "rerun" },
  { key: "audited", label: "Audited", interaction: "rerun" },
  { key: "scored", label: "Scored", interaction: "rerun" },
  { key: "drafted", label: "Drafted", interaction: "rerun" },
  { key: "approved", label: "Approved", interaction: "review" },
  { key: "sent", label: "Sent", interaction: "review" },
  { key: "replied", label: "Replied", interaction: "detail" },
];

export const workflowRows: WorkflowLeadRow[] = [
  {
    id: "wf-1",
    leadName: "Zenith Corp",
    category: "Manufacturing",
    steps: {
      discovered: { status: "done", detail: "Google Places", timestampLabel: "Jul 12" },
      enriched: { status: "done", detail: "Verified, manual entry", timestampLabel: "Jul 12" },
      audited: { status: "done", detail: "3 findings", timestampLabel: "Jul 13" },
      scored: { status: "done", detail: "Hot (82)", timestampLabel: "Jul 13" },
      drafted: { status: "done", detail: "Email + call script", timestampLabel: "Jul 14" },
      approved: { status: "done", detail: "Approved by S. Khan", timestampLabel: "Jul 14" },
      sent: { status: "done", detail: "Delivered", timestampLabel: "Jul 15" },
      replied: { status: "done", detail: "Booked a call", timestampLabel: "Jul 18" },
    },
  },
  {
    id: "wf-2",
    leadName: "Global Tech",
    category: "IT Services",
    steps: {
      discovered: { status: "done", detail: "Google Places", timestampLabel: "Jul 14" },
      enriched: { status: "done", detail: "Pattern guess, unverified", timestampLabel: "Jul 14" },
      audited: { status: "done", detail: "5 findings", timestampLabel: "Jul 15" },
      scored: { status: "done", detail: "Warm (61)", timestampLabel: "Jul 15" },
      drafted: { status: "done", detail: "Email drafted", timestampLabel: "Jul 16" },
      approved: { status: "done", detail: "Approved by S. Khan", timestampLabel: "Jul 16" },
      sent: { status: "done", detail: "Delivered", timestampLabel: "Jul 17" },
      replied: { status: "not_started" },
    },
  },
  {
    id: "wf-3",
    leadName: "Acme Solutions",
    category: "Professional Services",
    steps: {
      discovered: { status: "done", detail: "CSV import", timestampLabel: "Jul 20" },
      enriched: { status: "done", detail: "Verified, manual entry", timestampLabel: "Jul 20" },
      audited: { status: "done", detail: "2 findings", timestampLabel: "Jul 21" },
      scored: { status: "done", detail: "Warm (58)", timestampLabel: "Jul 21" },
      drafted: { status: "done", detail: "Email drafted", timestampLabel: "Jul 22" },
      approved: { status: "done", detail: "Approved by S. Khan", timestampLabel: "Jul 22" },
      sent: { status: "pending", detail: "Awaiting daily quota" },
      replied: { status: "not_started" },
    },
  },
  {
    id: "wf-4",
    leadName: "Horizon Retail",
    category: "Retail",
    steps: {
      discovered: { status: "done", detail: "Google Places", timestampLabel: "Jul 22" },
      enriched: { status: "done", detail: "Verified, manual entry", timestampLabel: "Jul 22" },
      audited: { status: "done", detail: "4 findings", timestampLabel: "Jul 23" },
      scored: { status: "done", detail: "Hot (77)", timestampLabel: "Jul 23" },
      drafted: { status: "done", detail: "Email drafted", timestampLabel: "Jul 24" },
      approved: { status: "pending", detail: "Awaiting review" },
      sent: { status: "not_started" },
      replied: { status: "not_started" },
    },
  },
  {
    id: "wf-5",
    leadName: "Blue Ridge Logistics",
    category: "Logistics",
    steps: {
      discovered: { status: "done", detail: "LinkedIn", timestampLabel: "Jul 24" },
      enriched: { status: "done", detail: "Verified, manual entry", timestampLabel: "Jul 24" },
      audited: { status: "done", detail: "1 finding", timestampLabel: "Jul 25" },
      scored: { status: "done", detail: "Cool (34)", timestampLabel: "Jul 25" },
      drafted: { status: "not_started" },
      approved: { status: "not_started" },
      sent: { status: "not_started" },
      replied: { status: "not_started" },
    },
  },
  {
    id: "wf-6",
    leadName: "Summit Foods",
    category: "Food & Beverage",
    steps: {
      discovered: { status: "done", detail: "Google Places", timestampLabel: "Jul 25" },
      enriched: { status: "done", detail: "Verified, manual entry", timestampLabel: "Jul 25" },
      audited: { status: "done", detail: "6 findings", timestampLabel: "Jul 26" },
      scored: {
        status: "failed",
        detail: "Missing confidence_score input",
        timestampLabel: "Jul 26",
      },
      drafted: { status: "not_started" },
      approved: { status: "not_started" },
      sent: { status: "not_started" },
      replied: { status: "not_started" },
    },
  },
  {
    id: "wf-7",
    leadName: "Lakeside Dental",
    category: "Healthcare",
    steps: {
      discovered: { status: "done", detail: "Google Places", timestampLabel: "Jul 26" },
      enriched: {
        status: "failed",
        detail: "No candidate email found on site",
        timestampLabel: "Jul 26",
      },
      audited: { status: "not_started" },
      scored: { status: "not_started" },
      drafted: { status: "not_started" },
      approved: { status: "not_started" },
      sent: { status: "not_started" },
      replied: { status: "not_started" },
    },
  },
  {
    id: "wf-8",
    leadName: "Cedar Grove Realty",
    category: "Real Estate",
    steps: {
      discovered: { status: "done", detail: "Google Places", timestampLabel: "Jul 29" },
      enriched: { status: "in_progress", detail: "Scanning site for a contact address" },
      audited: { status: "not_started" },
      scored: { status: "not_started" },
      drafted: { status: "not_started" },
      approved: { status: "not_started" },
      sent: { status: "not_started" },
      replied: { status: "not_started" },
    },
  },
  {
    id: "wf-9",
    leadName: "Northgate Manufacturing",
    category: "Manufacturing",
    steps: {
      discovered: { status: "done", detail: "Referral", timestampLabel: "Jul 27" },
      enriched: { status: "done", detail: "Verified, manual entry", timestampLabel: "Jul 27" },
      audited: { status: "done", detail: "2 findings", timestampLabel: "Jul 28" },
      scored: { status: "done", detail: "Hot (85)", timestampLabel: "Jul 28" },
      drafted: {
        status: "failed",
        detail: "Sender physical address still a placeholder",
        timestampLabel: "Jul 28",
      },
      approved: { status: "not_started" },
      sent: { status: "not_started" },
      replied: { status: "not_started" },
    },
  },
  {
    id: "wf-10",
    leadName: "Maple Street Cafe",
    category: "Food & Beverage",
    steps: {
      discovered: { status: "done", detail: "Google Places", timestampLabel: "Jul 30" },
      enriched: { status: "not_started" },
      audited: { status: "not_started" },
      scored: { status: "not_started" },
      drafted: { status: "not_started" },
      approved: { status: "not_started" },
      sent: { status: "not_started" },
      replied: { status: "not_started" },
    },
  },
];
