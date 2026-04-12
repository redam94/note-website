"use client";

import { useRef, useEffect, useState } from "react";
import * as d3 from "d3";
import type { GraphData, GraphNode } from "@/types";

interface ForceGraphProps {
  data: GraphData;
  focusNodeId?: number | null;
  onNodeClick?: (slug: string) => void;
  width?: number;
  height?: number;
  mode?: "local" | "global";
}

interface SimNode extends d3.SimulationNodeDatum, GraphNode {}
interface SimLink extends d3.SimulationLinkDatum<SimNode> {
  relationship: string;
  confidence: number;
}

// Muted olive palette for relationships
const EDGE_COLORS: Record<string, string> = {
  supports: "#8a9a5b",
  contradicts: "#b5716d",
  defines: "#7b8fa3",
  example_of: "#b5a255",
  part_of: "#9b8fb5",
  references: "#a0a095",
};

export default function ForceGraph({
  data,
  focusNodeId,
  onNodeClick,
  width = 800,
  height = 600,
  mode = "global",
}: ForceGraphProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [hoveredNode, setHoveredNode] = useState<number | null>(null);

  useEffect(() => {
    if (!svgRef.current || !data.nodes.length) return;

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    let filteredNodes = data.nodes;
    let filteredEdges = data.edges;

    if (mode === "local" && focusNodeId) {
      const neighborIds = new Set<number>();
      neighborIds.add(focusNodeId);
      data.edges.forEach((e) => {
        if (e.source === focusNodeId) neighborIds.add(e.target);
        if (e.target === focusNodeId) neighborIds.add(e.source);
      });
      filteredNodes = data.nodes.filter((n) => neighborIds.has(n.id));
      filteredEdges = data.edges.filter(
        (e) => neighborIds.has(e.source) && neighborIds.has(e.target)
      );
    }

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
      .scaleExtent([0.1, 4])
      .on("zoom", (event) => g.attr("transform", event.transform));
    svg.call(zoom);

    const simulation = d3
      .forceSimulation(nodes)
      .force("link", d3.forceLink(links).id((d: any) => d.id).distance(100))
      .force("charge", d3.forceManyBody().strength(-200))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("collision", d3.forceCollide().radius(30));

    // Edges
    const link = g
      .append("g")
      .selectAll("line")
      .data(links)
      .join("line")
      .attr("stroke", (d) => EDGE_COLORS[d.relationship] || "#c0bdb5")
      .attr("stroke-opacity", 0.4)
      .attr("stroke-width", (d) => Math.max(0.8, d.confidence * 2));

    // Nodes — muted olive tones by level
    const node = g
      .append("g")
      .selectAll("circle")
      .data(nodes)
      .join("circle")
      .attr("r", (d) => Math.max(5, Math.min(16, 5 + d.degree * 1.5)))
      .attr("fill", (d) => {
        if (d.id === focusNodeId) return "#6b7c3f";
        const levelColors = ["#8a9a5b", "#a0a095", "#b5a255"];
        return levelColors[d.level - 1] || "#b5b0a3";
      })
      .attr("stroke", (d) => (d.id === focusNodeId ? "#5a6d2f" : "#e0ddd5"))
      .attr("stroke-width", (d) => (d.id === focusNodeId ? 2.5 : 1))
      .style("cursor", "pointer")
      .on("mouseenter", (_event, d) => {
        setHoveredNode(d.id);
        const neighborIds = new Set<number>();
        neighborIds.add(d.id);
        links.forEach((l) => {
          const s = (l.source as SimNode).id;
          const t = (l.target as SimNode).id;
          if (s === d.id) neighborIds.add(t);
          if (t === d.id) neighborIds.add(s);
        });
        node.attr("opacity", (n) => (neighborIds.has(n.id) ? 1 : 0.15));
        link.attr("stroke-opacity", (l) => {
          const s = (l.source as SimNode).id;
          const t = (l.target as SimNode).id;
          return s === d.id || t === d.id ? 0.7 : 0.04;
        });
        label.attr("opacity", (n) => (neighborIds.has(n.id) ? 1 : 0.08));
      })
      .on("mouseleave", () => {
        setHoveredNode(null);
        node.attr("opacity", 1);
        link.attr("stroke-opacity", 0.4);
        label.attr("opacity", 0.8);
      })
      .on("click", (_event, d) => onNodeClick?.(d.slug))
      .call(
        d3
          .drag<SVGCircleElement, SimNode>()
          .on("start", (event, d) => {
            if (!event.active) simulation.alphaTarget(0.3).restart();
            d.fx = d.x;
            d.fy = d.y;
          })
          .on("drag", (event, d) => { d.fx = event.x; d.fy = event.y; })
          .on("end", (event, d) => {
            if (!event.active) simulation.alphaTarget(0);
            d.fx = null;
            d.fy = null;
          }) as any
      );

    // Labels
    const label = g
      .append("g")
      .selectAll("text")
      .data(nodes)
      .join("text")
      .text((d) => d.title)
      .attr("font-size", 11)
      .attr("dx", 14)
      .attr("dy", 4)
      .attr("fill", "#7a7565")
      .attr("opacity", 0.8)
      .attr("class", "select-none pointer-events-none");

    simulation.on("tick", () => {
      link
        .attr("x1", (d) => (d.source as SimNode).x!)
        .attr("y1", (d) => (d.source as SimNode).y!)
        .attr("x2", (d) => (d.target as SimNode).x!)
        .attr("y2", (d) => (d.target as SimNode).y!);
      node.attr("cx", (d) => d.x!).attr("cy", (d) => d.y!);
      label.attr("x", (d) => d.x!).attr("y", (d) => d.y!);
    });

    return () => { simulation.stop(); };
  }, [data, focusNodeId, width, height, mode, onNodeClick]);

  return (
    <div className="relative w-full h-full overflow-hidden bg-[var(--bg)]">
      <svg ref={svgRef} width={width} height={height} className="w-full h-full" viewBox={`0 0 ${width} ${height}`} />
      <div className="absolute bottom-3 left-3 bg-[var(--surface)]/95 rounded-md p-2 text-xs space-y-1 border border-[var(--border)]">
        {Object.entries(EDGE_COLORS).map(([type, color]) => (
          <div key={type} className="flex items-center gap-2">
            <span className="inline-block w-3 h-0.5 rounded" style={{ backgroundColor: color }} />
            <span className="text-[var(--text-secondary)]">{type.replace("_", " ")}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
