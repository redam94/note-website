"use client";

import { useRef, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import * as d3 from "d3";
import ForceGraph from "./ForceGraph";
import type { GraphData, GraphNode } from "@/types";

interface LocalGraphProps {
  data: GraphData;
  focusNodeId: number;
  width?: number;
  height?: number;
}

interface SimNode extends d3.SimulationNodeDatum, GraphNode {}
interface SimLink extends d3.SimulationLinkDatum<SimNode> {
  relationship: string;
  confidence: number;
}

export default function LocalGraph({
  data,
  focusNodeId,
  width = 280,
  height = 200,
}: LocalGraphProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const router = useRouter();
  const [modalOpen, setModalOpen] = useState(false);

  useEffect(() => {
    if (!svgRef.current || !data.nodes.length) return;

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    const neighborIds = new Set<number>();
    neighborIds.add(focusNodeId);
    data.edges.forEach((e) => {
      if (e.source === focusNodeId) neighborIds.add(e.target);
      if (e.target === focusNodeId) neighborIds.add(e.source);
    });

    const filteredNodes = data.nodes.filter((n) => neighborIds.has(n.id));
    const filteredEdges = data.edges.filter(
      (e) => neighborIds.has(e.source) && neighborIds.has(e.target)
    );

    const nodes: SimNode[] = filteredNodes.map((n) => ({ ...n }));
    const nodeMap = new Map(nodes.map((n) => [n.id, n]));

    const links: SimLink[] = filteredEdges
      .filter((e) => nodeMap.has(e.source) && nodeMap.has(e.target))
      .map((e) => ({
        source: nodeMap.get(e.source)!,
        target: nodeMap.get(e.target)!,
        relationship: e.relationship,
        confidence: e.confidence,
      }));

    const g = svg.append("g");

    const zoom = d3
      .zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.3, 3])
      .on("zoom", (event) => g.attr("transform", event.transform));
    svg.call(zoom);

    const simulation = d3
      .forceSimulation(nodes)
      .force("link", d3.forceLink(links).id((d: any) => d.id).distance(50))
      .force("charge", d3.forceManyBody().strength(-100))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("collision", d3.forceCollide().radius(18));

    g.append("g")
      .selectAll("line")
      .data(links)
      .join("line")
      .attr("stroke", "#d0cdc5")
      .attr("stroke-opacity", 0.3)
      .attr("stroke-width", 1);

    const node = g
      .append("g")
      .selectAll("circle")
      .data(nodes)
      .join("circle")
      .attr("r", (d) => (d.id === focusNodeId ? 5.5 : 3.5))
      .attr("fill", (d) => (d.id === focusNodeId ? "#6b7c3f" : "#b5b0a3"))
      .attr("stroke", (d) => (d.id === focusNodeId ? "#5a6d2f" : "none"))
      .attr("stroke-width", (d) => (d.id === focusNodeId ? 2 : 0))
      .attr("stroke-opacity", 0.3)
      .style("cursor", "pointer")
      .on("click", (_event, d) => router.push(`/notes/${d.slug}`))
      .call(
        d3
          .drag<SVGCircleElement, SimNode>()
          .on("start", (event, d) => {
            if (!event.active) simulation.alphaTarget(0.3).restart();
            d.fx = d.x; d.fy = d.y;
          })
          .on("drag", (event, d) => { d.fx = event.x; d.fy = event.y; })
          .on("end", (event, d) => {
            if (!event.active) simulation.alphaTarget(0);
            d.fx = null; d.fy = null;
          }) as any
      );

    node.append("title").text((d) => d.title);

    const linkEl = g.selectAll("line");

    simulation.on("tick", () => {
      linkEl
        .attr("x1", (d: any) => d.source.x)
        .attr("y1", (d: any) => d.source.y)
        .attr("x2", (d: any) => d.target.x)
        .attr("y2", (d: any) => d.target.y);
      node.attr("cx", (d) => d.x!).attr("cy", (d) => d.y!);
    });

    return () => { simulation.stop(); };
  }, [data, focusNodeId, width, height, router]);

  return (
    <>
      <div className="local-graph-container relative overflow-hidden group cursor-pointer"
        onClick={() => setModalOpen(true)}>
        <svg ref={svgRef} width={width} height={height} className="w-full" viewBox={`0 0 ${width} ${height}`} />
        {/* Expand icon */}
        <button
          className="absolute top-2 right-2 p-1 rounded bg-[var(--surface)]/80 text-[var(--muted)] hover:text-[var(--accent)] opacity-0 group-hover:opacity-100 transition-opacity"
          title="Expand graph"
          onClick={(e) => { e.stopPropagation(); setModalOpen(true); }}
        >
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4" />
          </svg>
        </button>
      </div>

      {/* Modal */}
      {modalOpen && (
        <div className="fixed inset-0 z-50 flex flex-col" style={{ background: "rgba(250, 249, 246, 0.97)" }}>
          <div className="flex items-center justify-between px-5 py-3 border-b border-[var(--border)]">
            <div className="flex items-center gap-3">
              <h2 className="text-[14px] font-semibold text-[var(--heading)]">Graph View</h2>
              <span className="text-[12px] text-[var(--muted)]">
                {data.nodes.length} nodes · {data.edges.length} edges
              </span>
            </div>
            <button
              onClick={() => setModalOpen(false)}
              className="p-1.5 text-[var(--muted)] hover:text-[var(--text)] rounded hover:bg-[var(--surface2)] transition-colors"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
          <div className="flex-1">
            <GraphModal data={data} focusNodeId={focusNodeId} onNodeClick={(slug) => { setModalOpen(false); router.push(`/notes/${slug}`); }} />
          </div>
        </div>
      )}
    </>
  );
}

function GraphModal({ data, focusNodeId, onNodeClick }: { data: GraphData; focusNodeId: number; onNodeClick: (slug: string) => void }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [dims, setDims] = useState({ width: 800, height: 600 });

  useEffect(() => {
    function update() {
      if (containerRef.current) {
        setDims({ width: containerRef.current.clientWidth, height: containerRef.current.clientHeight });
      }
    }
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);

  return (
    <div ref={containerRef} className="w-full h-full">
      <ForceGraph data={data} focusNodeId={focusNodeId} onNodeClick={onNodeClick} width={dims.width} height={dims.height} mode="global" />
    </div>
  );
}
