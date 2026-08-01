import {
  GitBranch,
  Plus,
  Search,
  Sparkles,
  Workflow,
  Zap,
} from "lucide-react";
import { Fragment } from "react";

import { PanelHeader } from "@/components/dashboard/PanelHeader";
import type {
  RecentWorkflow,
  WorkflowCanvasEdge,
  WorkflowCanvasNode,
  WorkflowLibraryItem,
  WorkflowNodeKind,
} from "@/types/dashboard";

interface WorkflowNodeBuilderProps {
  library: WorkflowLibraryItem[];
  nodes: WorkflowCanvasNode[];
  edges: WorkflowCanvasEdge[];
  recentWorkflows: RecentWorkflow[];
}

const KIND_ICON: Record<WorkflowNodeKind, typeof Zap> = {
  trigger: Zap,
  action: Sparkles,
  condition: GitBranch,
};

const KIND_STYLES: Record<WorkflowNodeKind, string> = {
  trigger: "border-emerald-300 bg-emerald-50 text-emerald-700",
  action: "border-blue-300 bg-blue-50 text-blue-700",
  condition: "border-amber-300 bg-amber-50 text-amber-700",
};

/** Look up a node's canvas position by id, defaulting to the origin if missing. */
function findNode(nodes: WorkflowCanvasNode[], nodeId: string): WorkflowCanvasNode | undefined {
  return nodes.find((node) => node.id === nodeId);
}

/**
 * "AI Workflow Automation Node Builder" panel: a node-library sidebar, a
 * static preview canvas (mock trigger/condition/action graph -- not
 * draggable in this phase), and a "Recent workflows" list. Named export per
 * AGENTS.md section 4.1.
 */
export function WorkflowNodeBuilder({
  library,
  nodes,
  edges,
  recentWorkflows,
}: WorkflowNodeBuilderProps): JSX.Element {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Workflow className="h-4 w-4 text-slate-500" aria-hidden />
          <h2 className="text-sm font-semibold text-slate-800">AI Workflow Automation Node Builder</h2>
        </div>
        <button
          type="button"
          className="flex items-center gap-1 rounded-md bg-blue-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-blue-700"
        >
          <Plus className="h-3.5 w-3.5" aria-hidden />
          New workflow
        </button>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[160px_1fr_160px]">
        <div>
          <div className="mb-3 flex items-center gap-1.5 rounded-md border border-slate-200 px-2 py-1.5">
            <Search className="h-3.5 w-3.5 text-slate-400" aria-hidden />
            <input
              type="text"
              placeholder="Search"
              className="w-full text-xs text-slate-600 outline-none placeholder:text-slate-400"
              readOnly
            />
          </div>
          <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
            Library
          </p>
          <ul className="flex flex-col gap-1 text-xs text-slate-600">
            {library.map((item) => (
              <li key={item.id} className="flex items-center gap-1.5 rounded px-1.5 py-1 hover:bg-slate-50">
                <Plus className="h-3 w-3 text-slate-400" aria-hidden />
                {item.label}
              </li>
            ))}
          </ul>
        </div>

        <div className="relative min-h-[220px] rounded-lg border border-dashed border-slate-200 bg-[radial-gradient(circle,_#e2e8f0_1px,_transparent_1px)] bg-[length:16px_16px]">
          <svg className="pointer-events-none absolute inset-0 h-full w-full" aria-hidden>
            {edges.map((edge) => {
              const from = findNode(nodes, edge.fromNodeId);
              const to = findNode(nodes, edge.toNodeId);
              if (!from || !to) {
                return null;
              }
              return (
                <Fragment key={edge.id}>
                  <line
                    x1={`${from.x + 14}%`}
                    y1={`${from.y + 8}%`}
                    x2={`${to.x}%`}
                    y2={`${to.y + 8}%`}
                    stroke="#94a3b8"
                    strokeWidth={1.5}
                  />
                </Fragment>
              );
            })}
          </svg>

          {nodes.map((node) => {
            const Icon = KIND_ICON[node.kind];
            return (
              <div
                key={node.id}
                className={`absolute w-[130px] rounded-md border px-2.5 py-1.5 text-[11px] shadow-sm ${KIND_STYLES[node.kind]}`}
                style={{ left: `${node.x}%`, top: `${node.y}%` }}
              >
                <div className="flex items-center gap-1.5 font-medium">
                  <Icon className="h-3 w-3" aria-hidden />
                  {node.title}
                </div>
                <p className="mt-0.5 font-semibold">{node.subtitle}</p>
              </div>
            );
          })}
        </div>

        <div>
          <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
            Recent workflows
          </p>
          <ul className="flex flex-col gap-1 text-xs text-slate-600">
            {recentWorkflows.map((workflow) => (
              <li key={workflow.id} className="truncate rounded px-1.5 py-1 hover:bg-slate-50">
                {workflow.label}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}
