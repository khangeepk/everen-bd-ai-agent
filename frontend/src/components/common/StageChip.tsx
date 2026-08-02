import { HelpCircle } from "lucide-react";

import { Tooltip } from "@/components/common/Tooltip";
import { type PipelineStage, stageTooltip } from "@/lib/plainLanguage";

interface StageChipProps {
  /** Backend pipeline stage value. */
  stage: PipelineStage;
}

/**
 * A pipeline-stage chip whose tooltip explains, in plain language, what the
 * stage means and what happens next. Named export per AGENTS.md section 4.1.
 */
export function StageChip({ stage }: StageChipProps): JSX.Element {
  const { label, explains } = stageTooltip(stage);
  return (
    <Tooltip content={explains}>
      <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
        {label}
        <HelpCircle className="h-3 w-3 text-slate-400" aria-hidden />
      </span>
    </Tooltip>
  );
}
