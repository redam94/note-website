"use client";

import { useEffect, useState, useMemo } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/components/AuthProvider";
import { useSpace } from "@/contexts/SpaceContext";
import { apiUrl } from "@/lib/api";
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

interface Comment {
  id: number;
  noteId: number;
  author: string;
  content: string;
  resolved: boolean;
  createdAt: string;
}

// ── Component ────────────────────────────────────────────────────────

export default function NoteViewer({ slug }: NoteViewerProps) {
  const [note, setNote] = useState<NoteWithLinks | null>(null);
  const [graphData, setGraphData] = useState<GraphData>({ nodes: [], edges: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [reprocessing, setReprocessing] = useState(false);
  const [reprocessResult, setReprocessResult] = useState<string | null>(null);
  const [reprocessInstructions, setReprocessInstructions] = useState("");
  const [showReprocessForm, setShowReprocessForm] = useState(false);
  const [comments, setComments] = useState<Comment[]>([]);
  const [newComment, setNewComment] = useState("");
  const [submittingComment, setSubmittingComment] = useState(false);
  const router = useRouter();
  const { role } = useAuth();
  const { spaceSlug } = useSpace();
  const isAdmin = role === "admin";
  const isSignedIn = role !== "guest";

  useEffect(() => {
    setLoading(true);
    setError(null);
    setConfirmDelete(false);
    setShowReprocessForm(false);
    setReprocessResult(null);
    Promise.all([
      fetch(apiUrl(`/api/notes/${slug}`, spaceSlug)).then((r) => (r.ok ? r.json() : Promise.reject("Not found"))),
      fetch(apiUrl("/api/graph", spaceSlug)).then((r) => (r.ok ? r.json() : { nodes: [], edges: [] })),
      fetch(apiUrl(`/api/notes/${slug}/comments`, spaceSlug)).then((r) => (r.ok ? r.json() : [])),
    ])
      .then(([noteData, graph, commentsData]) => {
        setNote(noteData);
        setGraphData(graph);
        setComments(commentsData);
      })
      .catch((err) => setError(typeof err === "string" ? err : "Failed to load note"))
      .finally(() => setLoading(false));
  }, [slug, spaceSlug]);

  const toc = useMemo(() => (note ? extractToc(note.content) : []), [note]);
  const fmTags = useMemo(() => (note ? extractFrontmatterTags(note.content) : []), [note]);
  const docType = useMemo(() => (note ? extractDocType(note.content) : null), [note]);
  const readTime = useMemo(() => (note ? estimateReadTime(note.content) : 0), [note]);

  async function handleDelete() {
    if (!confirmDelete) { setConfirmDelete(true); return; }
    setDeleting(true);
    try {
      const res = await fetch(apiUrl(`/api/notes/${slug}`, spaceSlug), { method: "DELETE" });
      if (!res.ok) throw new Error();
      window.dispatchEvent(new Event("sidebar-refresh"));
      router.push("/");
    } catch {
      setDeleting(false);
      setConfirmDelete(false);
    }
  }

  async function handleReprocess() {
    setReprocessing(true);
    setReprocessResult(null);
    try {
      const res = await fetch(apiUrl(`/api/notes/${slug}/reprocess`, spaceSlug), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ instructions: reprocessInstructions }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      setReprocessResult(
        `Reprocessed: ${(data.issues || []).join(", ") || "refined"}${data.comments_resolved ? ` (${data.comments_resolved} comments resolved)` : ""}`
      );
      setShowReprocessForm(false);
      setReprocessInstructions("");
      // Reload note
      const noteRes = await fetch(apiUrl(`/api/notes/${slug}`, spaceSlug));
      if (noteRes.ok) setNote(await noteRes.json());
      // Reload comments
      const commentsRes = await fetch(apiUrl(`/api/notes/${slug}/comments`, spaceSlug));
      if (commentsRes.ok) setComments(await commentsRes.json());
    } catch (e: any) {
      setReprocessResult(`Failed: ${e.message}`);
    } finally {
      setReprocessing(false);
    }
  }

  async function handleAddComment() {
    if (!newComment.trim()) return;
    setSubmittingComment(true);
    try {
      const res = await fetch(apiUrl(`/api/notes/${slug}/comments`, spaceSlug), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: newComment }),
      });
      if (res.ok) {
        const comment = await res.json();
        setComments((prev) => [...prev, comment]);
        setNewComment("");
      }
    } finally {
      setSubmittingComment(false);
    }
  }

  async function handleResolveComment(commentId: number) {
    const res = await fetch(apiUrl(`/api/notes/comments/${commentId}/resolve`, spaceSlug), {
      method: "POST",
    });
    if (res.ok) {
      setComments((prev) => prev.map((c) => c.id === commentId ? { ...c, resolved: true } : c));
    }
  }

  async function handleDeleteComment(commentId: number) {
    const res = await fetch(apiUrl(`/api/notes/comments/${commentId}`, spaceSlug), {
      method: "DELETE",
    });
    if (res.ok) {
      setComments((prev) => prev.filter((c) => c.id !== commentId));
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

  const openComments = comments.filter((c) => !c.resolved);
  const resolvedComments = comments.filter((c) => c.resolved);

  return (
    <div className="flex min-h-screen">
      {/* ── Center ── */}
      <div className="flex-1 max-w-[780px] mx-auto px-4 py-4 md:px-6 md:py-6">
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
            <div className="flex-shrink-0 mt-1 flex items-center gap-1">
              {isAdmin && (
                <>
                  {/* Reprocess button */}
                  <button
                    onClick={() => setShowReprocessForm(!showReprocessForm)}
                    className="p-1 text-[var(--muted)] hover:text-[var(--accent)] transition-colors"
                    title="Reprocess note"
                  >
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                    </svg>
                  </button>
                  {/* Delete button */}
                  {confirmDelete ? (
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
                </>
              )}
            </div>
          </div>

          <p className="text-[13px] text-[var(--text-secondary)] mt-1.5">
            {new Date(note.createdAt).toLocaleDateString("en-US", {
              year: "numeric", month: "short", day: "numeric",
            })}
            <span className="mx-2 text-[var(--border)]">·</span>
            {readTime} min read
            {openComments.length > 0 && (
              <>
                <span className="mx-2 text-[var(--border)]">·</span>
                <span className="text-[var(--callout-amber)]">{openComments.length} comment{openComments.length !== 1 ? "s" : ""}</span>
              </>
            )}
          </p>

          {allTags.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-3">
              {allTags.map((tag) => (
                <span key={tag} className="tag-pill">#{tag}</span>
              ))}
            </div>
          )}
        </div>

        {/* Reprocess form */}
        {showReprocessForm && isAdmin && (
          <div className="mb-5 p-4 bg-[var(--surface)] border border-[var(--border)] rounded-lg">
            <h3 className="text-[13px] font-semibold text-[var(--heading)] mb-2">Reprocess Note</h3>
            <p className="text-[12px] text-[var(--text-secondary)] mb-3">
              This will rewrite the note using the original source material. Open comments will be addressed and resolved.
            </p>
            <textarea
              value={reprocessInstructions}
              onChange={(e) => setReprocessInstructions(e.target.value)}
              placeholder="Optional: specific instructions for reprocessing..."
              className="w-full px-3 py-2 text-[13px] bg-[var(--bg)] border border-[var(--border)] rounded text-[var(--text)] placeholder-[var(--muted)] focus:outline-none focus:border-[var(--accent-light)] resize-none"
              rows={3}
            />
            <div className="flex items-center gap-2 mt-2">
              <button
                onClick={handleReprocess}
                disabled={reprocessing}
                className="px-3 py-1.5 text-[12px] font-medium bg-[var(--accent)] text-white rounded hover:opacity-90 disabled:opacity-50 transition-opacity"
              >
                {reprocessing ? (
                  <span className="inline-flex items-center gap-2">
                    <span className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    Reprocessing...
                  </span>
                ) : "Reprocess"}
              </button>
              <button
                onClick={() => { setShowReprocessForm(false); setReprocessInstructions(""); }}
                className="px-3 py-1.5 text-[12px] bg-[var(--surface2)] text-[var(--text-secondary)] rounded hover:bg-[var(--border)] transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {/* Reprocess result */}
        {reprocessResult && (
          <div className={`mb-5 p-3 rounded text-[13px] ${
            reprocessResult.startsWith("Failed")
              ? "bg-[var(--danger)]/5 text-[var(--danger)]"
              : "bg-[var(--accent-bg)] text-[var(--accent)]"
          }`}>
            {reprocessResult}
          </div>
        )}

        {/* Content */}
        <NoteContent content={note.content} className="text-[15px] leading-[1.8]" />

        {/* Comments section */}
        <div className="mt-10 pt-6 border-t border-[var(--border)]">
          <h2 className="text-[16px] font-semibold text-[var(--heading)] mb-4">
            Comments
            {openComments.length > 0 && (
              <span className="ml-2 text-[12px] font-normal text-[var(--callout-amber)]">
                {openComments.length} open
              </span>
            )}
          </h2>

          {/* Comment list */}
          {comments.length > 0 ? (
            <div className="space-y-3 mb-4">
              {comments.map((c) => (
                <div
                  key={c.id}
                  className={`p-3 rounded-lg border ${
                    c.resolved
                      ? "bg-[var(--bg)] border-[var(--border-light)] opacity-60"
                      : "bg-[var(--surface)] border-[var(--border)]"
                  }`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-2">
                      <span className={`text-[11px] px-1.5 py-0.5 rounded font-medium ${
                        c.author === "admin"
                          ? "bg-[var(--accent-bg)] text-[var(--accent)]"
                          : "bg-[var(--surface2)] text-[var(--text-secondary)]"
                      }`}>
                        {c.author}
                      </span>
                      <span className="text-[11px] text-[var(--muted)]">
                        {new Date(c.createdAt).toLocaleDateString("en-US", { month: "short", day: "numeric" })}
                      </span>
                      {c.resolved && (
                        <span className="text-[10px] text-[var(--callout-green)]">resolved</span>
                      )}
                    </div>
                    {isAdmin && (
                      <div className="flex items-center gap-1">
                        {!c.resolved && (
                          <button
                            onClick={() => handleResolveComment(c.id)}
                            className="text-[var(--muted)] hover:text-[var(--callout-green)] transition-colors"
                            title="Resolve"
                          >
                            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                            </svg>
                          </button>
                        )}
                        <button
                          onClick={() => handleDeleteComment(c.id)}
                          className="text-[var(--muted)] hover:text-[var(--danger)] transition-colors"
                          title="Delete"
                        >
                          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                          </svg>
                        </button>
                      </div>
                    )}
                  </div>
                  <p className="text-[13px] text-[var(--text)]">{c.content}</p>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-[12px] text-[var(--muted)] mb-4">No comments yet.</p>
          )}

          {/* Add comment form */}
          {isSignedIn && (
            <div className="flex gap-2">
              <input
                type="text"
                value={newComment}
                onChange={(e) => setNewComment(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") handleAddComment(); }}
                placeholder="Add a comment or suggestion..."
                className="flex-1 px-3 py-2 text-[13px] bg-[var(--bg)] border border-[var(--border)] rounded text-[var(--text)] placeholder-[var(--muted)] focus:outline-none focus:border-[var(--accent-light)]"
              />
              <button
                onClick={handleAddComment}
                disabled={submittingComment || !newComment.trim()}
                className="px-3 py-2 text-[12px] font-medium bg-[var(--accent)] text-white rounded hover:opacity-90 disabled:opacity-50 transition-opacity flex-shrink-0"
              >
                {submittingComment ? "..." : "Post"}
              </button>
            </div>
          )}
        </div>
      </div>

      {/* ── Right sidebar ── */}
      <div className="w-[300px] flex-shrink-0 border-l border-[var(--border)] hidden lg:block">
        <div className="sticky top-0 h-screen overflow-y-auto p-4 space-y-6">
          {/* Graph */}
          <div>
            <h3 className="text-[11px] font-semibold text-[var(--muted)] uppercase tracking-wider mb-2">
              Graph View
            </h3>
            {graphData.nodes.length > 0 && (
              <LocalGraph data={graphData} focusNodeId={note.id} width={268} height={190} />
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
