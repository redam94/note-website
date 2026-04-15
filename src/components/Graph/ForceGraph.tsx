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
  showLegend?: boolean;
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
  cluster_link: "#6b8f9a",
};

// Node colors by note type (from type/* tags)
const NODE_TYPE_COLORS: Record<string, string> = {
  concept:    "#7a8c4e",  // olive green
  definition: "#7b6b9e",  // purple
  theorem:    "#4a7c9b",  // steel blue
  example:    "#b5a255",  // amber
  paper:      "#9a7a5a",  // warm brown
  textbook:   "#6b7c3f",  // dark olive
  tutorial:   "#5a9a7a",  // teal
  qa:         "#9e8432",  // gold
  index:      "#a0a095",  // gray
};
const DEFAULT_NODE_COLOR = "#8a9a5b";

function getNodeType(tags: string[]): string {
  for (const tag of tags) {
    if (tag.startsWith("type/")) return tag.slice(5);
  }
  return "";
}

function getNodeColor(d: GraphNode): string {
  if (d.nodeType === "subgraph") return "#5a7a8a";
  const noteType = getNodeType(d.tags || []);
  return NODE_TYPE_COLORS[noteType] || DEFAULT_NODE_COLOR;
}

export default function ForceGraph({
  data,
  focusNodeId,
  onNodeClick,
  width = 800,
  height = 600,
  mode = "global",
  showLegend = true,
}: ForceGraphProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [hoveredNode, setHoveredNode] = useState<number | null>(null);
  // Persist node positions and zoom between data updates so existing nodes
  // don't bounce when new nodes are added.
  const nodePositionsRef = useRef<Map<number, { x: number; y: number }>>(new Map());
  const zoomTransformRef = useRef<d3.ZoomTransform | null>(null);

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

    // Build a neighbor map so new nodes can be seeded near a connected node
    const edgeNeighbors = new Map<number, number[]>();
    filteredEdges.forEach((e) => {
      if (!edgeNeighbors.has(e.source)) edgeNeighbors.set(e.source, []);
      if (!edgeNeighbors.has(e.target)) edgeNeighbors.set(e.target, []);
      edgeNeighbors.get(e.source)!.push(e.target);
      edgeNeighbors.get(e.target)!.push(e.source);
    });

    const posCache = nodePositionsRef.current;
    const nodes: SimNode[] = filteredNodes.map((n) => {
      const cached = posCache.get(n.id);
      if (cached) {
        // Existing node — restore last known position so it doesn't move
        return { ...n, x: cached.x, y: cached.y };
      }
      // New node — try to seed near a connected neighbor that has a position
      const neighbors = edgeNeighbors.get(n.id) ?? [];
      for (const nbId of neighbors) {
        const nbPos = posCache.get(nbId);
        if (nbPos) {
          const angle = Math.random() * 2 * Math.PI;
          const r = 40 + Math.random() * 30;
          return { ...n, x: nbPos.x + Math.cos(angle) * r, y: nbPos.y + Math.sin(angle) * r };
        }
      }
      // No neighbor found — seed near center with small random jitter
      const angle = Math.random() * 2 * Math.PI;
      const r = Math.random() * 80;
      return { ...n, x: width / 2 + Math.cos(angle) * r, y: height / 2 + Math.sin(angle) * r };
    });

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
      .on("zoom", (event) => {
        zoomTransformRef.current = event.transform;
        g.attr("transform", event.transform);
      });
    svg.call(zoom);
    // Restore previous zoom/pan so the view doesn't jump on data updates
    if (zoomTransformRef.current) {
      svg.call(zoom.transform, zoomTransformRef.current);
    }

    // Use a lower starting alpha when most nodes are already positioned —
    // this keeps existing nodes nearly still while new ones settle in.
    const hasNewNodes = filteredNodes.some((n) => !posCache.has(n.id));
    const startAlpha = hasNewNodes && posCache.size > 0 ? 0.25 : 1;

    const simulation = d3
      .forceSimulation(nodes)
      .alpha(startAlpha)
      .force("link", d3.forceLink(links).id((d: any) => d.id).distance(60).strength(0.15))
      .force("charge", d3.forceManyBody().strength(-40).distanceMax(300))
      .force("center", d3.forceCenter(width / 2, height / 2).strength(0.03))
      .force("collision", d3.forceCollide().radius((d: any) => nodeRadius(d) + 2).strength(0.4))
      .alphaDecay(0.015);

    // Node radius helper
    function nodeRadius(d: SimNode): number {
      if (d.nodeType === "subgraph") return Math.max(6, Math.min(14, 6 + d.degree * 0.25));
      return Math.max(1.8, Math.min(7, 1.8 + Math.sqrt(d.degree) * 1.4));
    }

    // Edges — thin, translucent lines
    const link = g
      .append("g")
      .selectAll("line")
      .data(links)
      .join("line")
      .attr("stroke", "#c8c5bc")
      .attr("stroke-opacity", 0.15)
      .attr("stroke-width", 0.5);

    // Nodes — filled circles colored by note type
    const node = g
      .append("g")
      .selectAll("circle")
      .data(nodes)
      .join("circle")
      .attr("r", (d) => nodeRadius(d))
      .attr("fill", (d) => getNodeColor(d))
      .attr("stroke", (d) => d.id === focusNodeId ? "#2c2a1f" : "none")
      .attr("stroke-width", (d) => d.id === focusNodeId ? 2 : 0)
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
        node.attr("opacity", (n) => (neighborIds.has(n.id) ? 1 : 0.12));
        link
          .attr("stroke-opacity", (l) => {
            const s = (l.source as SimNode).id;
            const t = (l.target as SimNode).id;
            return s === d.id || t === d.id ? 0.5 : 0.03;
          })
          .attr("stroke", (l) => {
            const s = (l.source as SimNode).id;
            const t = (l.target as SimNode).id;
            return s === d.id || t === d.id
              ? (EDGE_COLORS[l.relationship] || "#8a9a5b")
              : "#c8c5bc";
          })
          .attr("stroke-width", (l) => {
            const s = (l.source as SimNode).id;
            const t = (l.target as SimNode).id;
            return s === d.id || t === d.id ? 1 : 0.5;
          });
        label.attr("opacity", (n) => (n.id === d.id ? 1 : 0));
      })
      .on("mouseleave", () => {
        setHoveredNode(null);
        node.attr("opacity", 1);
        link.attr("stroke-opacity", 0.15).attr("stroke", "#c8c5bc").attr("stroke-width", 0.5);
        label.attr("opacity", 0);
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
      .attr("font-size", 9)
      .attr("dx", (d) => nodeRadius(d) + 4)
      .attr("dy", 3)
      .attr("fill", "#5a5545")
      .attr("opacity", 0)
      .attr("class", "select-none pointer-events-none");

    simulation.on("tick", () => {
      // Persist every node's position so the next render can restore them
      nodes.forEach((n) => {
        if (n.x != null && n.y != null) {
          nodePositionsRef.current.set(n.id, { x: n.x, y: n.y });
        }
      });
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
      {showLegend && (
        <div className="absolute bottom-3 left-3 bg-[var(--surface)]/95 rounded-md p-2 text-xs space-y-1 border border-[var(--border)]">
          {Object.entries(NODE_TYPE_COLORS).map(([type, color]) => (
            <div key={type} className="flex items-center gap-2">
              <span className="inline-block w-2.5 h-2.5 rounded-full" style={{ backgroundColor: color }} />
              <span className="text-[var(--text-secondary)]">{type}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
