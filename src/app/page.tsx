"use client";

import { useEffect, useState, useRef } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import ForceGraph from "@/components/Graph/ForceGraph";
import NoteContent from "@/components/NoteContent";
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

export default function Home() {
  const [graphData, setGraphData] = useState<GraphData>({ nodes: [], edges: [] });
  const [notes, setNotes] = useState<NotePreview[]>([]);
  const [rootIndex, setRootIndex] = useState<RootIndex | null>(null);
  const [loading, setLoading] = useState(true);
  const [showGraph, setShowGraph] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 });
  const router = useRouter();

  useEffect(() => {
    // Fetch graph data and try to find root index
    Promise.all([
      fetch("/api/graph").then((r) => (r.ok ? r.json() : { nodes: [], edges: [] })),
      fetch("/api/search?q=Index%3A+Root").then((r) => (r.ok ? r.json() : [])),
    ]).then(([graph, searchResults]) => {
      setGraphData(graph);
      setNotes(
        graph.nodes
          .map((n: any) => ({ id: n.id, title: n.title, slug: n.slug, tags: n.tags || [], level: n.level }))
          .sort((a: NotePreview, b: NotePreview) => a.level - b.level || a.title.localeCompare(b.title))
      );
      // Find the root index note from search results
      const root = searchResults.find((r: any) => r.title === "Index: Root");
      if (root) {
        // Fetch full note content
        fetch(`/api/notes/${root.slug}`)
          .then((r) => (r.ok ? r.json() : null))
          .then((note) => {
            if (note) setRootIndex({ slug: note.slug, content: note.content, summary: note.summary });
          });
      }
    }).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    function update() {
      if (containerRef.current) setDimensions({ width: containerRef.current.clientWidth, height: containerRef.current.clientHeight });
    }
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, [showGraph]);

  const topLevel = notes.filter((n) => n.level <= 1);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <span className="inline-block w-5 h-5 border-2 border-[var(--accent)] border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (showGraph) {
    return (
      <div className="h-full flex flex-col p-4">
        <div className="flex items-center justify-between mb-3">
          <h1 className="text-lg font-semibold text-[var(--heading)]">Knowledge Graph</h1>
          <div className="flex items-center gap-3">
            <span className="text-[12px] text-[var(--muted)]">
              {graphData.nodes.length} nodes · {graphData.edges.length} edges
            </span>
            <button
              onClick={() => setShowGraph(false)}
              className="px-3 py-1 text-[12px] bg-[var(--surface)] text-[var(--text-secondary)] rounded border border-[var(--border)] hover:bg-[var(--surface2)] transition-colors"
            >
              Back to notes
            </button>
          </div>
        </div>
        <div ref={containerRef} className="flex-1 min-h-0 rounded-lg border border-[var(--border)] overflow-hidden">
          <ForceGraph data={graphData} onNodeClick={(slug) => router.push(`/notes/${slug}`)} width={dimensions.width} height={dimensions.height} mode="global" />
        </div>
      </div>
    );
  }

  // If a root index exists, redirect to its note page
  if (rootIndex) {
    return (
      <div className="max-w-[720px] mx-auto px-4 py-6 md:px-8 md:py-8">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-[28px] font-bold text-[var(--heading)] mb-1">Knowledge Base</h1>
            <p className="text-[13px] text-[var(--text-secondary)]">
              {notes.length} notes · {graphData.edges.length} connections
            </p>
          </div>
          <div className="flex gap-3">
            <button onClick={() => setShowGraph(true)} className="text-[13px] text-[var(--link)] hover:underline">
              Graph view
            </button>
            <Link href="/search" className="text-[13px] text-[var(--link)] hover:underline">Search</Link>
            <Link href="/upload" className="text-[13px] text-[var(--link)] hover:underline">Upload</Link>
          </div>
        </div>

        <NoteContent content={rootIndex.content} />
      </div>
    );
  }

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
          <button onClick={() => setShowGraph(true)} className="text-[13px] text-[var(--link)] hover:underline">
            Open graph view
          </button>
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

      {notes.length > topLevel.length && (
        <div className="mt-10">
          <h2 className="text-[20px] font-semibold text-[var(--heading)] mb-5 pb-2 border-b border-[var(--border)]">All Notes</h2>
          <div className="space-y-1">
            {notes.filter((n) => n.level > 1).map((note) => (
              <Link key={note.id} href={`/notes/${note.slug}`} className="block py-1.5 text-[14px] text-[var(--text-secondary)] hover:text-[var(--link)] transition-colors">
                {note.title}
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
