"use client";

import { useEffect, useState, useMemo } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/components/AuthProvider";
import LocalGraph from "@/components/Graph/LocalGraph";
import NoteContent from "@/components/NoteContent";
import type { NoteWithLinks, GraphData } from "@/types";

interface NoteViewerProps {
  slug: string;
}

// ── Helpers ──────────────────────────────────────────────────────────

interface TocEntry { id: string; text: string; level: number }

function extractToc(content: string): TocEntry[] {
  const entries: TocEntry[] = [];
  for (const line of content.split("\n")) {
    const m = line.match(/^(#{2,4})\s+(.+)/);
    if (m) {
      const raw = m[2].replace(/\*\*/g, "").replace(/`[^`]*`/g, "").trim();
      entries.push({ id: slugify(raw), text: raw, level: m[1].length });
    }
  }
  return entries;
}

function estimateReadTime(content: string): number {
  return Math.max(1, Math.round(content.split(/\s+/).length / 250));
}

function extractFrontmatterTags(content: string): string[] {
  const fmMatch = content.match(/^---\n([\s\S]*?)\n---/);
  if (!fmMatch) return [];
  const tags: string[] = [];
  let inTags = false;
  for (const line of fmMatch[1].split("\n")) {
    if (line.match(/^tags:\s*$/)) { inTags = true; continue; }
    if (inTags) {
      const tagMatch = line.match(/^\s+-\s+(.+)/);
      if (tagMatch) tags.push(tagMatch[1].trim());
      else inTags = false;
    }
  }
  return tags;
}

function extractDocType(content: string): string | null {
  const m = content.match(/^doc_type:\s*(.+)$/m);
  return m ? m[1].trim() : null;
}

// ── Component ────────────────────────────────────────────────────────

export default function NoteViewer({ slug }: NoteViewerProps) {
  const [note, setNote] = useState<NoteWithLinks | null>(null);
  const [graphData, setGraphData] = useState<GraphData>({ nodes: [], edges: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const router = useRouter();
  const { role } = useAuth();
  const isAdmin = role === "admin";

  useEffect(() => {
    setLoading(true);
    setError(null);
    setConfirmDelete(false);
    Promise.all([
      fetch(`/api/notes/${slug}`).then((r) => (r.ok ? r.json() : Promise.reject("Not found"))),
      fetch("/api/graph").then((r) => (r.ok ? r.json() : { nodes: [], edges: [] })),
    ])
      .then(([noteData, graph]) => { setNote(noteData); setGraphData(graph); })
      .catch((err) => setError(typeof err === "string" ? err : "Failed to load note"))
      .finally(() => setLoading(false));
  }, [slug]);

  const toc = useMemo(() => (note ? extractToc(note.content) : []), [note]);
  const fmTags = useMemo(() => (note ? extractFrontmatterTags(note.content) : []), [note]);
  const docType = useMemo(() => (note ? extractDocType(note.content) : null), [note]);
  const readTime = useMemo(() => (note ? estimateReadTime(note.content) : 0), [note]);

  async function handleDelete() {
    if (!confirmDelete) { setConfirmDelete(true); return; }
    setDeleting(true);
    try {
      const res = await fetch(`/api/notes/${slug}`, { method: "DELETE" });
      if (!res.ok) throw new Error();
      window.dispatchEvent(new Event("sidebar-refresh"));
      router.push("/");
    } catch {
      setDeleting(false);
      setConfirmDelete(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <span className="inline-block w-5 h-5 border-2 border-[var(--accent)] border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (error || !note) {
    return (
      <div className="p-12 text-center">
        <p className="text-[var(--danger)] mb-3">{error || "Note not found"}</p>
        <Link href="/" className="text-[var(--link)] hover:underline text-sm">Back to home</Link>
      </div>
    );
  }

  const breadcrumbs: { label: string; href?: string }[] = [{ label: "Home", href: "/" }];
  if (note.source) breadcrumbs.push({ label: note.source });
  if (note.chapter) breadcrumbs.push({ label: note.chapter });
  breadcrumbs.push({ label: note.title });

  const allTags = [...new Set([...fmTags, ...note.tags.map((t) => `topic/${t}`)])];
  if (docType) allTags.push(`doc/${docType}`);

  return (
    <div className="flex min-h-screen">
      {/* ── Center ── */}
      <div className="flex-1 max-w-[740px] mx-auto px-4 py-4 md:px-8 md:py-6">
        {/* Breadcrumb */}
        <nav className="flex items-center gap-1.5 text-[12px] text-[var(--muted)] mb-4 flex-wrap">
          {breadcrumbs.map((bc, idx) => (
            <span key={idx} className="flex items-center gap-1.5">
              {idx > 0 && <span className="text-[var(--border)]">›</span>}
              {bc.href ? (
                <Link href={bc.href} className="hover:text-[var(--link)] transition-colors">{bc.label}</Link>
              ) : idx < breadcrumbs.length - 1 ? (
                <span className="text-[var(--text-secondary)]">{bc.label}</span>
              ) : (
                <span className="text-[var(--text)]">{bc.label}</span>
              )}
            </span>
          ))}
        </nav>

        {/* Title block */}
        <div className="mb-5">
          <div className="flex items-start justify-between gap-4">
            <h1 className="text-[26px] font-bold text-[var(--heading)] leading-tight">
              {note.title}
            </h1>
            <div className="flex-shrink-0 mt-1">
              {!isAdmin ? null : confirmDelete ? (
                <div className="flex items-center gap-1.5">
                  <button onClick={handleDelete} disabled={deleting}
                    className="px-2 py-1 text-[11px] bg-[var(--danger)] text-white rounded hover:opacity-90">
                    {deleting ? "..." : "Delete"}
                  </button>
                  <button onClick={() => setConfirmDelete(false)}
                    className="px-2 py-1 text-[11px] bg-[var(--surface2)] text-[var(--text-secondary)] rounded hover:bg-[var(--border)]">
                    Cancel
                  </button>
                </div>
              ) : (
                <button onClick={handleDelete}
                  className="p-1 text-[var(--muted)] hover:text-[var(--danger)] transition-colors" title="Delete note">
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                  </svg>
                </button>
              )}
            </div>
          </div>

          <p className="text-[13px] text-[var(--text-secondary)] mt-1.5">
            {new Date(note.createdAt).toLocaleDateString("en-US", {
              year: "numeric", month: "short", day: "numeric",
            })}
            <span className="mx-2 text-[var(--border)]">·</span>
            {readTime} min read
          </p>

          {allTags.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-3">
              {allTags.map((tag) => (
                <span key={tag} className="tag-pill">#{tag}</span>
              ))}
            </div>
          )}
        </div>

        {/* Content */}
        <NoteContent content={note.content} className="text-[15px] leading-[1.8]" />
      </div>

      {/* ── Right sidebar ── */}
      <div className="w-[260px] flex-shrink-0 border-l border-[var(--border)] hidden lg:block">
        <div className="sticky top-0 h-screen overflow-y-auto p-4 space-y-6">
          {/* Graph */}
          <div>
            <h3 className="text-[11px] font-semibold text-[var(--muted)] uppercase tracking-wider mb-2">
              Graph View
            </h3>
            {graphData.nodes.length > 0 && (
              <LocalGraph data={graphData} focusNodeId={note.id} width={232} height={170} />
            )}
          </div>

          {/* TOC */}
          {toc.length > 0 && (
            <div>
              <h3 className="text-[11px] font-semibold text-[var(--muted)] uppercase tracking-wider mb-2">
                Table of Contents
              </h3>
              <nav>
                {toc.map((entry) => (
                  <a key={entry.id} href={`#${entry.id}`} className="toc-link"
                    style={{ paddingLeft: `${(entry.level - 2) * 12}px` }}>
                    {entry.text}
                  </a>
                ))}
              </nav>
            </div>
          )}

          {/* Backlinks */}
          {note.backlinks.length > 0 && (
            <div>
              <h3 className="text-[11px] font-semibold text-[var(--muted)] uppercase tracking-wider mb-2">Backlinks</h3>
              <ul className="space-y-1">
                {note.backlinks.map((link) => (
                  <li key={link.id}>
                    <Link href={`/notes/${link.slug}`} className="text-[13px] text-[var(--link)] hover:underline leading-snug block">
                      {link.title}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Outgoing */}
          {note.outlinks.length > 0 && (
            <div>
              <h3 className="text-[11px] font-semibold text-[var(--muted)] uppercase tracking-wider mb-2">Outgoing Links</h3>
              <ul className="space-y-1">
                {note.outlinks.map((link) => (
                  <li key={link.id}>
                    <Link href={`/notes/${link.slug}`} className="text-[13px] text-[var(--text-secondary)] hover:text-[var(--link)] transition-colors leading-snug block">
                      {link.title}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function slugify(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}
