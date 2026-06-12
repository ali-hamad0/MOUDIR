import type { DailyCost } from "../api/types";
import { t } from "../i18n";

// Per-agent bar fill colours (stable mapping so the legend stays consistent).
const AGENT_COLOURS: Record<string, string> = {
  supervisor: "#3b82f6",
  order: "#22c55e",
  inventory: "#f97316",
  finance: "#a855f7",
  customer: "#ec4899",
  advisor: "#14b8a6",
};
const FALLBACK_COLOUR = "#94a3b8";

function agentColour(name: string): string {
  return AGENT_COLOURS[name] ?? FALLBACK_COLOUR;
}

// Simple SVG stacked-bar chart — no external chart library. Shared between the
// owner cost dashboard and the founder's per-tenant drill-down (same data shape).
export function CostChart({
  days,
  budgetUsd,
}: {
  days: DailyCost[];
  budgetUsd: number;
}) {
  const W = 720;
  const H = 200;
  const PAD = { top: 16, right: 8, bottom: 32, left: 48 };
  const chartW = W - PAD.left - PAD.right;
  const chartH = H - PAD.top - PAD.bottom;

  const maxVal = Math.max(
    ...days.map((d) => d.total_usd),
    budgetUsd > 0 ? budgetUsd : 0,
    0.0001,
  );

  const barW = chartW / days.length;
  const gap = Math.max(1, barW * 0.15);
  const effectiveW = barW - gap;

  const yScale = (v: number) => chartH - (v / maxVal) * chartH;

  // Y-axis ticks
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => ({
    y: yScale(f * maxVal),
    label: (f * maxVal).toFixed(4),
  }));

  // Collect agent names for legend
  const agentNames = Array.from(new Set(days.flatMap((d) => Object.keys(d.by_agent))));

  return (
    <div className="overflow-x-auto">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        style={{ minWidth: 480 }}
        aria-label={t.costsSubtitle}
      >
        <g transform={`translate(${PAD.left},${PAD.top})`}>
          {/* Y-axis ticks */}
          {ticks.map((tk) => (
            <g key={tk.label}>
              <line x1={0} y1={tk.y} x2={chartW} y2={tk.y} stroke="#e2e8f0" strokeWidth={1} />
              <text x={-4} y={tk.y + 4} textAnchor="end" fontSize={9} fill="#94a3b8">
                {tk.label}
              </text>
            </g>
          ))}

          {/* Bars */}
          {days.map((day, i) => {
            const x = i * barW + gap / 2;
            let stackY = chartH;
            return (
              <g key={day.date}>
                {Object.entries(day.by_agent).map(([agent, val]) => {
                  const barH = (val / maxVal) * chartH;
                  stackY -= barH;
                  return (
                    <rect
                      key={agent}
                      x={x}
                      y={stackY}
                      width={effectiveW}
                      height={barH}
                      fill={agentColour(agent)}
                      rx={1}
                    >
                      <title>{`${agent}: $${val.toFixed(6)}`}</title>
                    </rect>
                  );
                })}
                {/* X-axis label — every 5th bar */}
                {i % 5 === 0 && (
                  <text
                    x={x + effectiveW / 2}
                    y={chartH + 14}
                    textAnchor="middle"
                    fontSize={8}
                    fill="#94a3b8"
                  >
                    {day.date.slice(5)}
                  </text>
                )}
              </g>
            );
          })}

          {/* Budget line */}
          {budgetUsd > 0 && (
            <line
              x1={0}
              y1={yScale(budgetUsd)}
              x2={chartW}
              y2={yScale(budgetUsd)}
              stroke="#ef4444"
              strokeWidth={1.5}
              strokeDasharray="4 3"
            />
          )}
        </g>
      </svg>

      {/* Legend */}
      {agentNames.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-3 px-2 text-xs text-muted-foreground">
          {agentNames.map((a) => (
            <span key={a} className="flex items-center gap-1">
              <span
                className="inline-block h-2.5 w-2.5 rounded-sm"
                style={{ background: agentColour(a) }}
              />
              {a}
            </span>
          ))}
          {budgetUsd > 0 && (
            <span className="flex items-center gap-1">
              <span className="inline-block h-2.5 w-2.5 rounded-sm bg-red-500 opacity-70" />
              {t.costsBudgetLabel}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
