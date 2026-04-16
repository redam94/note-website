"use client";

import { useCallback, useEffect, useState, useRef, useMemo } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import ForceGraph from "@/components/Graph/ForceGraph";
import NoteContent from "@/components/NoteContent";
import { useSpace } from "@/contexts/SpaceContext";
import { apiUrl } from "@/lib/api";
import type { GraphData } from "@/types";

interface NotePreview {
  id: number;
  title: string;
  slug: string;
  tags: string[];
  level: number;
}

interface RootIndex {
  slug: string;
  content: string;
  summary: string | null;
}

interface TocEntry { id: string; text: string; level: number }

function extractToc(content: string): TocEntry[] {
  const entries: TocEntry[] = [];
  const stripped = content.replace(/^---[\s\S]*?---\n*/m, "");
  for (const line of stripped.split("\n")) {
    const m = line.match(/^(#{2,4})\s+(.+)/);
    if (m) {
      const raw = m[2].replace(/\*\*/g, "").replace(/`[^`]*`/g, "").trim();
      const id = raw.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
      entries.push({ id, text: raw, level: m[1].length });
    }
  }
  return entries;
}

export default function Home() {
  const [graphData, setGraphData] = useState<GraphData>({ nodes: [], edges: [] });
  const [notes, setNotes] = useState<NotePreview[]>([]);
  const [rootIndex, setRootIndex] = useState<RootIndex | null>(null);
  const [loading, setLoading] = useState(true);
  const graphContainerRef = useRef<HTMLDivElement>(null);
  const [graphDims, setGraphDims] = useState({ width: 300, height: 220 });
  const router = useRouter();
  const { spaceSlug } = useSpace();

  useEffect(() => {
    Promise.all([
      fetch(apiUrl("/api/graph", spaceSlug)).then((r) => (r.ok ? r.json() : { nodes: [], edges: [] })),
      fetch(apiUrl("/api/search", spaceSlug, { q: "Index: Root" })).then((r) => (r.ok ? r.json() : [])),
    ]).then(([graph, searchResults]) => {
      setGraphData(graph);
      setNotes(
        graph.nodes
          .map((n: any) => ({ id: n.id, title: n.title, slug: n.slug, tags: n.tags || [], level: n.level }))
          .sort((a: NotePreview, b: NotePreview) => a.level - b.level || a.title.localeCompare(b.title))
      );
      const root = searchResults.find((r: any) => r.title === "Index: Root");
      if (root) {
        fetch(apiUrl(`/api/notes/${root.slug}`, spaceSlug))
          .then((r) => (r.ok ? r.json() : null))
          .then((note) => {
            if (note) setRootIndex({ slug: note.slug, content: note.content, summary: note.summary });
          });
      }
    }).finally(() => setLoading(false));
  }, [spaceSlug]);

  // Measure graph container
  useEffect(() => {
    function update() {
      if (graphContainerRef.current) {
        setGraphDims({
          width: graphContainerRef.current.clientWidth,
          height: 220,
        });
      }
    }
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, [rootIndex]);

  const toc = useMemo(() => (rootIndex ? extractToc(rootIndex.content) : []), [rootIndex]);
  const handleNodeClick = useCallback((slug: string) => router.push(`/notes/${slug}`), [router]);
  const topLevel = notes.filter((n) => n.level <= 1);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <span className="inline-block w-5 h-5 border-2 border-[var(--accent)] border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  // Root index exists — show two-column layout with graph + TOC sidebar
  if (rootIndex) {
    return (
      <div className="flex min-h-screen">
        {/* Center content */}
        <div className="flex-1 max-w-[740px] mx-auto px-4 py-4 md:px-8 md:py-6">
          <div className="mb-5">
            <h1 className="text-[26px] font-bold text-[var(--heading)] leading-tight">
              Knowledge Base
            </h1>
            <p className="text-[13px] text-[var(--text-secondary)] mt-1.5">
              {notes.length} notes · {graphData.edges.length} connections
            </p>
          </div>

          <NoteContent content={rootIndex.content} className="text-[15px] leading-[1.8]" />
        </div>

        {/* Right sidebar — graph + TOC */}
        <div className="w-[280px] flex-shrink-0 border-l border-[var(--border)] hidden lg:block">
          <div className="sticky top-0 h-screen overflow-y-auto p-4 space-y-6">
            {/* Graph */}
            {graphData.nodes.length > 0 && (
              <div>
                <h3 className="text-[11px] font-semibold text-[var(--muted)] uppercase tracking-wider mb-2">
                  Knowledge Graph
                </h3>
                <div
                  ref={graphContainerRef}
                  className="rounded-lg border border-[var(--border)] overflow-hidden"
                >
                  <ForceGraph
                    data={graphData}
                    onNodeClick={handleNodeClick}
                    width={graphDims.width}
                    height={graphDims.height}
                    mode="global"
                    showLegend={false}
                  />
                </div>
                <p className="text-[10px] text-[var(--muted)] mt-1.5 text-center">
                  {graphData.nodes.length} nodes · {graphData.edges.length} edges
                </p>
              </div>
            )}

            {/* TOC */}
            {toc.length > 0 && (
              <div>
                <h3 className="text-[11px] font-semibold text-[var(--muted)] uppercase tracking-wider mb-2">
                  Table of Contents
                </h3>
                <nav>
                  {toc.map((entry) => (
                    <a
                      key={entry.id}
                      href={`#${entry.id}`}
                      className="toc-link"
                      style={{ paddingLeft: `${(entry.level - 2) * 12}px` }}
                    >
                      {entry.text}
                    </a>
                  ))}
                </nav>
              </div>
            )}

            {/* Quick links */}
            <div>
              <h3 className="text-[11px] font-semibold text-[var(--muted)] uppercase tracking-wider mb-2">
                Quick Links
              </h3>
              <div className="space-y-1.5">
                <Link href="/search" className="block text-[13px] text-[var(--link)] hover:underline">
                  Search notes
                </Link>
                <Link href="/upload" className="block text-[13px] text-[var(--link)] hover:underline">
                  Upload document
                </Link>
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // No root index — fallback layout
  return (
    <div className="max-w-[720px] mx-auto px-4 py-6 md:px-8 md:py-8">
      <div className="mb-8">
        <h1 className="text-[28px] font-bold text-[var(--heading)] mb-1">Second Brain</h1>
        <p className="text-[13px] text-[var(--text-secondary)]">
          {notes.length} notes · {graphData.edges.length} connections
        </p>
      </div>

      <div className="callout callout-abstract mb-8">
        <div className="callout-title">About</div>
        <p className="text-[14px] text-[var(--text-secondary)] leading-relaxed mt-1">
          A structured knowledge base of ingested documents, extracted notes, and cross-linked concepts.
          Use the graph view or search bar to explore connections across the collection.
        </p>
        <div className="flex gap-3 mt-3">
          <Link href="/search" className="text-[13px] text-[var(--link)] hover:underline">Search notes</Link>
          <Link href="/upload" className="text-[13px] text-[var(--link)] hover:underline">Upload document</Link>
        </div>
      </div>

      {topLevel.length > 0 && (
        <div>
          <h2 className="text-[20px] font-semibold text-[var(--heading)] mb-5 pb-2 border-b border-[var(--border)]">Topics</h2>
          <div className="space-y-4">
            {topLevel.map((note) => (
              <Link key={note.id} href={`/notes/${note.slug}`} className="block group">
                <h3 className="text-[16px] font-medium text-[var(--link)] group-hover:underline">
                  {note.title}
                </h3>
                {note.tags.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mt-1">
                    {note.tags.slice(0, 4).map((tag) => (
                      <span key={tag} className="text-[11px] text-[var(--muted)]">#{tag}</span>
                    ))}
                  </div>
                )}
              </Link>
            ))}
          </div>
        </div>
      )}

      {notes.length === 0 && (
        <div className="text-center py-16">
          <p className="text-[var(--muted)] text-[15px] mb-3">No notes yet</p>
          <Link href="/upload" className="text-[var(--link)] hover:underline text-[14px]">Upload a document to get started</Link>
        </div>
      )}
    </div>
  );
}
