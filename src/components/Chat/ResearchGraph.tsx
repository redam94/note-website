"use client";

import { useRef, useEffect, useState } from "react";
import Link from "next/link";
import * as d3 from "d3";
import { useSpace } from "@/contexts/SpaceContext";
import { apiUrl } from "@/lib/api";
import type { GraphData } from "@/types";
import type { SourceNote } from "./ChatMessage";

// ── Colours (mirrors ForceGraph.tsx) ─────────────────────────────────
const NODE_TYPE_COLORS: Record<string, string> = {
  concept:    "#7a8c4e",
  definition: "#7b6b9e",
  theorem:    "#4a7c9b",
  example:    "#b5a255",
  paper:      "#9a7a5a",
  textbook:   "#6b7c3f",
  tutorial:   "#5a9a7a",
  qa:         "#9e8432",
  index:      "#a0a095",
};
const DEFAULT_COLOR = "#8a9a5b";
const DIMMED_COLOR  = "#d0cdc5";

const EDGE_COLORS: Record<string, string> = {
  supports:    "#8a9a5b",
  contradicts: "#b5716d",
  defines:     "#7b8fa3",
  example_of:  "#b5a255",
  part_of:     "#9b8fb5",
  references:  "#a0a095",
};

function getNodeColor(tags: string[]): string {
  for (const tag of tags) {
    if (tag.startsWith("type/")) return NODE_TYPE_COLORS[tag.slice(5)] ?? DEFAULT_COLOR;
  }
  return DEFAULT_COLOR;
}

// ── Types ─────────────────────────────────────────────────────────────
interface SimNode extends d3.SimulationNodeDatum {
  id: number;
  title: string;
  slug: string;
  tags: string[];
  degree: number;
}
interface SimLink extends d3.SimulationLinkDatum<SimNode> {
  relationship: string;
}

// Extracted so both effects and hover handlers share the same formula
function nodeRadius(d: SimNode, highlighted: Set<number>, latest: Set<number>): number {
  if (latest.has(d.id))      return Math.max(5, Math.min(9,  3   + Math.sqrt(d.degree) * 1.3));
  if (highlighted.has(d.id)) return Math.max(4, Math.min(7,  2.5 + Math.sqrt(d.degree) * 1.0));
  return Math.max(2, Math.min(4, 1.5 + Math.sqrt(d.degree) * 0.4));
}

// ── Props ─────────────────────────────────────────────────────────────
export interface ResearchGraphProps {
  /** All note IDs that have appeared as sources across the whole conversation */
  highlightedIds: Set<number>;
  /** Note IDs from the most recent query only (subset of highlightedIds) */
  latestQueryIds: Set<number>;
  isSearching: boolean;
  sources: SourceNote[];
  statusMessages: string[];
}

// ── Component ─────────────────────────────────────────────────────────
export default function ResearchGraph({
  highlightedIds,
  latestQueryIds,
  isSearching,
  sources,
  statusMessages,
}: ResearchGraphProps) {
  const { spaceSlug } = useSpace();
  const [fullGraph, setFullGraph] = useState<GraphData | null>(null);
  const svgRef  = useRef<SVGSVGElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [dims, setDims] = useState({ width: 380, height: 280 });

  // Refs so hover/drag callbacks always read the latest prop values
  // without needing to rebuild the simulation.
  const highlightedRef = useRef(highlightedIds);
  const latestRef      = useRef(latestQueryIds);
  highlightedRef.current = highlightedIds;
  latestRef.current      = latestQueryIds;

  // D3 selection refs — updated without restarting the simulation
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const nodeSelRef  = useRef<any>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const linkSelRef  = useRef<any>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const labelSelRef = useRef<any>(null);

  // Fetch the full knowledge graph once per space
  useEffect(() => {
    setFullGraph(null);
    fetch(apiUrl("/api/graph", spaceSlug, { include_topics: "true" }))
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => { if (data) setFullGraph(data); })
      .catch(() => {});
  }, [spaceSlug]);

  // Track container size
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      setDims({ width: el.clientWidth || 380, height: el.clientHeight || 280 });
    });
    ro.observe(el);
    setDims({ width: el.clientWidth || 380, height: el.clientHeight || 280 });
    return () => ro.disconnect();
  }, []);

  // ── Build simulation (only when fullGraph or dims change) ────────────
  useEffect(() => {
    nodeSelRef.current  = null;
    linkSelRef.current  = null;
    labelSelRef.current = null;

    if (!svgRef.current || !fullGraph?.nodes.length) return;

    const { width, height } = dims;
    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    const hIds = highlightedRef.current;
    const lIds = latestRef.current;

    const nodes: SimNode[] = fullGraph.nodes.map((n) => ({
      id:     n.id,
      title:  n.title,
      slug:   n.slug,
      tags:   n.tags ?? [],
      degree: n.degree,
    }));
    const nodeMap = new Map(nodes.map((n) => [n.id, n]));

    const links: SimLink[] = (fullGraph.edges ?? [])
      .filter((e) => nodeMap.has(e.source as number) && nodeMap.has(e.target as number))
      .map((e) => ({
        source:       nodeMap.get(e.source as number)!,
        target:       nodeMap.get(e.target as number)!,
        relationship: e.relationship,
      }));

    const g = svg.append("g");

    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.1, 4])
      .on("zoom", (ev) => g.attr("transform", ev.transform));
    svg.call(zoom);

    const simulation = d3
      .forceSimulation(nodes)
      .force("link",      d3.forceLink(links).id((d: any) => d.id).distance(55).strength(0.2))
      .force("charge",    d3.forceManyBody().strength(-40).distanceMax(250))
      .force("center",    d3.forceCenter(width / 2, height / 2).strength(0.05))
      .force("collision", d3.forceCollide().radius(8).strength(0.5))
      .alphaDecay(0.02);

    // Edges
    const link = g.append("g")
      .selectAll("line")
      .data(links)
      .join("line")
      .attr("stroke", (d) => {
        const s = (d.source as SimNode).id;
        const t = (d.target as SimNode).id;
        return hIds.has(s) && hIds.has(t) ? (EDGE_COLORS[d.relationship] ?? DEFAULT_COLOR) : "#d0cdc5";
      })
      .attr("stroke-opacity", (d) => {
        const s = (d.source as SimNode).id;
        const t = (d.target as SimNode).id;
        return hIds.has(s) && hIds.has(t) ? 0.45 : 0.15;
      })
      .attr("stroke-width", (d) => {
        const s = (d.source as SimNode).id;
        const t = (d.target as SimNode).id;
        return hIds.has(s) && hIds.has(t) ? 1 : 0.5;
      });

    // Nodes
    const node = g.append("g")
      .selectAll("circle")
      .data(nodes)
      .join("circle")
      .attr("r",            (d) => nodeRadius(d, hIds, lIds))
      .attr("fill",         (d) => hIds.has(d.id) ? getNodeColor(d.tags) : DIMMED_COLOR)
      .attr("stroke",       (d) => lIds.has(d.id) ? "#fff" : "none")
      .attr("stroke-width", (d) => lIds.has(d.id) ? 1.5 : 0)
      .attr("opacity",      (d) => hIds.has(d.id) ? 1 : 0.35)
      .style("cursor", "pointer")
      // ── Hover ──────────────────────────────────────────────────────
      .on("mouseenter", (_ev, d) => {
        const curH = highlightedRef.current;
        const curL = latestRef.current;

        // Build 1-hop neighbour set
        const nbrIds = new Set<number>([d.id]);
        links.forEach((l) => {
          const s = (l.source as SimNode).id;
          const t = (l.target as SimNode).id;
          if (s === d.id) nbrIds.add(t);
          if (t === d.id) nbrIds.add(s);
        });

        // Dim non-neighbours, highlight neighbours
        node
          .attr("opacity", (n: SimNode) => nbrIds.has(n.id) ? 1 : 0.08)
          .attr("r",       (n: SimNode) =>
            n.id === d.id
              ? nodeRadius(n, curH, curL) * 1.4
              : nodeRadius(n, curH, curL)
          );

        link
          .attr("stroke-opacity", (l: any) => {
            const s = (l.source as SimNode).id;
            const t = (l.target as SimNode).id;
            return s === d.id || t === d.id ? 0.65 : 0.03;
          })
          .attr("stroke", (l: any) => {
            const s = (l.source as SimNode).id;
            const t = (l.target as SimNode).id;
            return s === d.id || t === d.id
              ? (EDGE_COLORS[l.relationship] ?? DEFAULT_COLOR)
              : "#d0cdc5";
          })
          .attr("stroke-width", (l: any) => {
            const s = (l.source as SimNode).id;
            const t = (l.target as SimNode).id;
            return s === d.id || t === d.id ? 1.5 : 0.5;
          });

        // Show label only for the hovered node
        label.attr("opacity", (n: SimNode) => n.id === d.id ? 1 : 0);
      })
      .on("mouseleave", () => {
        const curH = highlightedRef.current;
        const curL = latestRef.current;

        // Restore to the highlighted/dimmed baseline
        node
          .attr("opacity", (n: SimNode) => curH.has(n.id) ? 1 : 0.35)
          .attr("r",       (n: SimNode) => nodeRadius(n, curH, curL));

        link
          .attr("stroke-opacity", (l: any) => {
            const s = (l.source as SimNode).id;
            const t = (l.target as SimNode).id;
            return curH.has(s) && curH.has(t) ? 0.45 : 0.15;
          })
          .attr("stroke", (l: any) => {
            const s = (l.source as SimNode).id;
            const t = (l.target as SimNode).id;
            return curH.has(s) && curH.has(t)
              ? (EDGE_COLORS[l.relationship] ?? DEFAULT_COLOR)
              : "#d0cdc5";
          })
          .attr("stroke-width", (l: any) => {
            const s = (l.source as SimNode).id;
            const t = (l.target as SimNode).id;
            return curH.has(s) && curH.has(t) ? 1 : 0.5;
          });

        label.attr("opacity", (n: SimNode) => curL.has(n.id) ? 0.9 : 0);
      })
      .on("click", (_ev, d) => window.open(`/notes/${d.slug}`, "_blank"))
      // ── Drag ───────────────────────────────────────────────────────
      .call(
        d3.drag<SVGCircleElement, SimNode>()
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

    // Labels — only for latest-query nodes initially
    const label = g.append("g")
      .selectAll("text")
      .data(nodes)
      .join("text")
      .text((d) => d.title.length > 24 ? d.title.slice(0, 22) + "…" : d.title)
      .attr("font-size", 7.5)
      .attr("dx", (d) => nodeRadius(d, hIds, lIds) + 3)
      .attr("dy", 3)
      .attr("fill", "#5a5545")
      .attr("opacity", (d) => lIds.has(d.id) ? 0.9 : 0)
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

    // Stash selections for the styling effect
    nodeSelRef.current  = node;
    linkSelRef.current  = link;
    labelSelRef.current = label;

    return () => { simulation.stop(); };
  }, [fullGraph, dims]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Update visual styling without restarting the simulation ──────────
  useEffect(() => {
    const node  = nodeSelRef.current;
    const link  = linkSelRef.current;
    const label = labelSelRef.current;
    if (!node) return;

    node
      .transition().duration(350)
      .attr("r",            (d: SimNode) => nodeRadius(d, highlightedIds, latestQueryIds))
      .attr("fill",         (d: SimNode) => highlightedIds.has(d.id) ? getNodeColor(d.tags) : DIMMED_COLOR)
      .attr("stroke",       (d: SimNode) => latestQueryIds.has(d.id) ? "#fff" : "none")
      .attr("stroke-width", (d: SimNode) => latestQueryIds.has(d.id) ? 1.5 : 0)
      .attr("opacity",      (d: SimNode) => highlightedIds.has(d.id) ? 1 : 0.35);

    if (link) {
      link
        .transition().duration(350)
        .attr("stroke", (d: any) => {
          const s = (d.source as SimNode).id;
          const t = (d.target as SimNode).id;
          return highlightedIds.has(s) && highlightedIds.has(t)
            ? (EDGE_COLORS[d.relationship] ?? DEFAULT_COLOR)
            : "#d0cdc5";
        })
        .attr("stroke-opacity", (d: any) => {
          const s = (d.source as SimNode).id;
          const t = (d.target as SimNode).id;
          return highlightedIds.has(s) && highlightedIds.has(t) ? 0.45 : 0.15;
        })
        .attr("stroke-width", (d: any) => {
          const s = (d.source as SimNode).id;
          const t = (d.target as SimNode).id;
          return highlightedIds.has(s) && highlightedIds.has(t) ? 1 : 0.5;
        });
    }

    if (label) {
      label
        .transition().duration(350)
        .attr("opacity", (d: SimNode) => latestQueryIds.has(d.id) ? 0.9 : 0);
    }
  }, [highlightedIds, latestQueryIds]);

  const hasGraph    = fullGraph !== null && fullGraph.nodes.length > 0;
  const showSources = sources.length > 0;

  return (
    <div className="flex flex-col h-full bg-[var(--bg)]">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-[var(--border)] flex-shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-semibold text-[var(--muted)] uppercase tracking-wider">
            Knowledge Graph
          </span>
          {isSearching && (
            <span className="flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-[var(--accent)] animate-pulse" />
              <span className="text-[10px] text-[var(--accent)]">searching</span>
            </span>
          )}
        </div>
        {fullGraph && (
          <span className="text-[10px] text-[var(--muted)]">
            {highlightedIds.size > 0 && (
              <span className="text-[var(--accent)] mr-1">{highlightedIds.size} in chat ·</span>
            )}
            {fullGraph.nodes.length} total
          </span>
        )}
      </div>

      {/* Graph area */}
      <div ref={wrapRef} className="relative flex-1 min-h-0 overflow-hidden">
        {!hasGraph && (
          <div className="absolute inset-0 flex items-center justify-center">
            <p className="text-[11px] text-[var(--muted)] text-center px-6 leading-relaxed">
              {fullGraph === null
                ? "Loading knowledge graph…"
                : "No notes yet — upload documents to get started."}
            </p>
          </div>
        )}

        <svg
          ref={svgRef}
          width={dims.width}
          height={dims.height}
          className="w-full h-full"
          viewBox={`0 0 ${dims.width} ${dims.height}`}
        />

        {/* Status log overlay */}
        {isSearching && statusMessages.length > 0 && (
          <div className="absolute bottom-2 left-0 right-0 px-3 space-y-1 pointer-events-none">
            {statusMessages.slice(-3).map((msg, i) => (
              <div key={i} className="flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-[var(--accent)] animate-pulse flex-shrink-0" />
                <span className="text-[10px] text-[var(--muted)] truncate">{msg}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Source notes list */}
      {showSources && (
        <div className="border-t border-[var(--border)] flex flex-col flex-shrink-0" style={{ maxHeight: "42%" }}>
          <p className="text-[10px] font-semibold text-[var(--muted)] px-3 pt-2 pb-1 uppercase tracking-wider flex-shrink-0">
            Sources ({sources.length})
          </p>
          <div className="overflow-y-auto px-2 pb-2 space-y-0.5">
            {sources.map((s) => (
              <Link
                key={s.id}
                href={`/notes/${s.slug}`}
                className="flex items-start gap-2 px-2 py-1.5 rounded-md hover:bg-[var(--surface2)] transition-colors group"
              >
                <svg
                  className="w-3 h-3 mt-0.5 text-[var(--muted)] group-hover:text-[var(--accent)] flex-shrink-0 transition-colors"
                  fill="none" stroke="currentColor" viewBox="0 0 24 24"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                    d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
                <div className="min-w-0">
                  <p className="text-[11px] font-medium text-[var(--heading)] group-hover:text-[var(--accent)] truncate leading-tight transition-colors">
                    {s.title}
                  </p>
                  {(s.chapter || s.page != null) && (
                    <p className="text-[10px] text-[var(--muted)] truncate">
                      {[s.chapter, s.page != null ? `p. ${s.page}` : null].filter(Boolean).join(" · ")}
                    </p>
                  )}
                </div>
              </Link>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
