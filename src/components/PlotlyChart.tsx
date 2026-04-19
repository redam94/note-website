"use client";

import dynamic from "next/dynamic";
import { useMemo } from "react";

const Plot = dynamic(
  async () => {
    const [factoryMod, Plotly] = await Promise.all([
      import("react-plotly.js/factory"),
      // @ts-expect-error — no types shipped for the cartesian dist
      import("plotly.js-cartesian-dist-min"),
    ]);
    return factoryMod.default(Plotly.default ?? Plotly);
  },
  {
    ssr: false,
    loading: () => (
      <div className="plotly-placeholder my-4 flex h-[320px] items-center justify-center rounded border border-[var(--border)] bg-[var(--surface)] text-[12px] text-[var(--muted)]">
        Loading chart…
      </div>
    ),
  },
);

interface PlotlyChartProps {
  source: string;
}

interface ParsedSpec {
  data: any[];
  layout?: Record<string, any>;
}

function tryParse(source: string): ParsedSpec | null {
  try {
    const parsed = JSON.parse(source);
    if (!parsed || typeof parsed !== "object") return null;
    if (!Array.isArray((parsed as ParsedSpec).data)) return null;
    return parsed as ParsedSpec;
  } catch {
    return null;
  }
}

export default function PlotlyChart({ source }: PlotlyChartProps) {
  const spec = useMemo(() => tryParse(source), [source]);

  if (!spec) {
    return (
      <details className="plotly-error my-4 rounded border border-amber-400/40 bg-amber-500/5 p-3 text-[12px]">
        <summary className="cursor-pointer text-amber-400">
          Chart could not be rendered (invalid Plotly JSON)
        </summary>
        <pre className="mt-2 overflow-x-auto font-mono text-[11px] text-[var(--text-secondary)]">
          {source}
        </pre>
      </details>
    );
  }

  const layout = {
    autosize: true,
    margin: { l: 50, r: 20, t: 40, b: 50 },
    paper_bgcolor: "transparent",
    plot_bgcolor: "transparent",
    font: { family: "inherit", size: 12 },
    ...(spec.layout ?? {}),
  };

  return (
    <div className="plotly-container my-4 rounded border border-[var(--border)] bg-[var(--surface)] p-2">
      <Plot
        data={spec.data}
        layout={layout}
        style={{ width: "100%", height: "400px" }}
        useResizeHandler
        config={{ displayModeBar: false, responsive: true }}
      />
    </div>
  );
}
