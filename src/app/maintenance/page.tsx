"use client";

import { useState } from "react";
import Link from "next/link";
import { useSpace } from "@/contexts/SpaceContext";
import { apiUrl } from "@/lib/api";

interface AuditData {
  total_notes: number;
  missing_frontmatter: Array<{ id: number; title: string; issues: string[] }>;
  shallow_notes: Array<{ id: number; title: string; words: number }>;
  orphan_notes: Array<{ id: number; title: string; slug: string }>;
  broken_links: Array<{ note_id: number; note_title: string; broken_link: string }>;
  missing_indexes: string[];
}

interface RepairResult {
  note_id: number;
  title: string;
  changes: string[];
  new_links: string[];
}

export default function MaintenancePage() {
  const [audit, setAudit] = useState<AuditData | null>(null);
  const [loading, setLoading] = useState("");
  const [results, setResults] = useState<string[]>([]);
  const [repairResult, setRepairResult] = useState<RepairResult | null>(null);
  const [clusters, setClusters] = useState<Array<{ id: number; label: string; path?: string | null; level?: number; summary: string | null; member_count: number }>>([]);
  const { spaceSlug } = useSpace();

  async function runAudit() {
    setLoading("audit");
    setResults([]);
    try {
      const res = await fetch(apiUrl("/api/maintenance/audit", spaceSlug));
      if (!res.ok) throw new Error();
      setAudit(await res.json());
    } catch { setResults(["Audit failed"]); }
    finally { setLoading(""); }
  }

  async function pollJob(jobId: string, label: string) {
    let missCount = 0;
    const poll = setInterval(async () => {
      try {
        const res = await fetch(apiUrl(`/api/maintenance/jobs/${jobId}`, spaceSlug));
        if (!res.ok) return;
        const job = await res.json();

        // Job lost (server restarted)
        if (job.status === "not_found") {
          missCount++;
          if (missCount >= 3) {
            clearInterval(poll);
            setLoading("");
            setResults(["Job was interrupted by server restart. Try again."]);
          }
          return;
        }
        missCount = 0;

        setResults((prev) => {
          const base = prev.filter((r) => !r.startsWith("⏳"));
          return [`⏳ ${job.progress}`, ...base];
        });
        if (job.status === "done") {
          clearInterval(poll);
          setLoading("");
          const resultLines = [];
          if (job.result) {
            if (job.result.details) resultLines.push(...job.result.details);
            if (job.result.repaired !== undefined)
              resultLines.unshift(`Repaired ${job.result.repaired}, skipped ${job.result.skipped}`);
            if (job.result.indexes_created !== undefined)
              resultLines.unshift(`Created ${job.result.indexes_created}, updated ${job.result.indexes_updated}`);
          }
          setResults(resultLines.length > 0 ? resultLines : [job.progress]);
        } else if (job.status === "error") {
          clearInterval(poll);
          setLoading("");
          setResults([`Error: ${job.progress}`]);
        }
      } catch { /* ignore poll errors */ }
    }, 2000);
  }

  async function runReindex() {
    setLoading("reindex");
    setResults([]);
    try {
      const res = await fetch(apiUrl("/api/maintenance/reindex", spaceSlug), { method: "POST" });
      const data = await res.json();
      if (data.job_id) {
        await pollJob(data.job_id, "Reindex");
      }
    } catch { setResults(["Reindex failed"]); setLoading(""); }
  }

  async function runFixLinks() {
    setLoading("fix-links");
    setResults([]);
    try {
      const res = await fetch(apiUrl("/api/maintenance/fix-links", spaceSlug), { method: "POST" });
      const data = await res.json();
      setResults([`Applied ${data.fixes_applied} fixes across ${data.notes_scanned} notes`]);
    } catch { setResults(["Fix links failed"]); }
    finally { setLoading(""); }
  }

  async function runRepairShallow() {
    setLoading("repair-shallow");
    setResults([]);
    try {
      const res = await fetch(apiUrl("/api/maintenance/repair-shallow", spaceSlug), { method: "POST" });
      const data = await res.json();
      if (data.job_id) {
        await pollJob(data.job_id, "Repair");
      }
    } catch { setResults(["Repair failed"]); setLoading(""); }
  }

  async function repairNote(noteId: number) {
    setLoading(`repair-${noteId}`);
    setRepairResult(null);
    try {
      const res = await fetch(apiUrl("/api/maintenance/repair", spaceSlug), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note_id: noteId }),
      });
      setRepairResult(await res.json());
    } catch { setResults(["Repair failed"]); }
    finally { setLoading(""); }
  }

  async function runCleanupIndexes() {
    setLoading("cleanup");
    setResults([]);
    try {
      const res = await fetch(apiUrl("/api/maintenance/cleanup-indexes", spaceSlug), { method: "POST" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setResults([
        `Removed ${data.removed} orphaned index notes`,
        ...(data.details || []),
      ]);
    } catch (e: any) {
      setResults([`Cleanup failed: ${e.message}`]);
    } finally {
      setLoading("");
    }
  }

  async function runCommunityDetection() {
    setLoading("community");
    setResults([]);
    setClusters([]);
    try {
      const res = await fetch(apiUrl("/api/community/detect", spaceSlug), { method: "POST" });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      setClusters(data.clusters || []);
      setResults([data.message || `Detected ${(data.clusters || []).length} communities`]);
    } catch (e: any) {
      setResults([`Community detection failed: ${e.message}`]);
    } finally {
      setLoading("");
    }
  }

  const btnClass = (active: string) =>
    `px-4 py-2.5 text-[13px] font-medium rounded-lg border transition-all ${
      loading === active
        ? "bg-[var(--accent-bg)] border-[var(--accent)] text-[var(--accent)]"
        : "bg-[var(--bg)] border-[var(--border)] text-[var(--text-secondary)] hover:border-[var(--accent-light)] hover:text-[var(--accent)]"
    }`;

  return (
    <div className="max-w-[740px] mx-auto px-4 py-6 md:px-8 md:py-8">
      <h1 className="text-[24px] font-bold text-[var(--heading)] mb-2">
        Note Maintenance
      </h1>
      <p className="text-[var(--text-secondary)] text-[14px] mb-8">
        Audit, repair, and reindex your knowledge base
      </p>

      {/* Action buttons */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-8">
        <button onClick={runAudit} disabled={!!loading} className={btnClass("audit")}>
          {loading === "audit" ? (
            <span className="inline-flex items-center gap-2">
              <span className="w-3 h-3 border-2 border-[var(--accent)]/30 border-t-[var(--accent)] rounded-full animate-spin" />
              Auditing...
            </span>
          ) : (
            <>
              <span className="block text-[15px] mb-0.5">Audit Vault</span>
              <span className="text-[11px] opacity-70">Scan for structural issues</span>
            </>
          )}
        </button>

        <button onClick={runReindex} disabled={!!loading} className={btnClass("reindex")}>
          {loading === "reindex" ? (
            <span className="inline-flex items-center gap-2">
              <span className="w-3 h-3 border-2 border-[var(--accent)]/30 border-t-[var(--accent)] rounded-full animate-spin" />
              Reindexing...
            </span>
          ) : (
            <>
              <span className="block text-[15px] mb-0.5">Reindex</span>
              <span className="text-[11px] opacity-70">Create/update folder indexes</span>
            </>
          )}
        </button>

        <button onClick={runFixLinks} disabled={!!loading} className={btnClass("fix-links")}>
          {loading === "fix-links" ? (
            <span className="inline-flex items-center gap-2">
              <span className="w-3 h-3 border-2 border-[var(--accent)]/30 border-t-[var(--accent)] rounded-full animate-spin" />
              Fixing...
            </span>
          ) : (
            <>
              <span className="block text-[15px] mb-0.5">Fix Cross-Links</span>
              <span className="text-[11px] opacity-70">Sync depends_on / used_by</span>
            </>
          )}
        </button>

        <button onClick={runRepairShallow} disabled={!!loading} className={btnClass("repair-shallow")}>
          {loading === "repair-shallow" ? (
            <span className="inline-flex items-center gap-2">
              <span className="w-3 h-3 border-2 border-[var(--accent)]/30 border-t-[var(--accent)] rounded-full animate-spin" />
              Repairing...
            </span>
          ) : (
            <>
              <span className="block text-[15px] mb-0.5">Repair Shallow Notes</span>
              <span className="text-[11px] opacity-70">Enrich notes with &lt;150 words</span>
            </>
          )}
        </button>

        <button onClick={runCleanupIndexes} disabled={!!loading} className={btnClass("cleanup")}>
          {loading === "cleanup" ? (
            <span className="inline-flex items-center gap-2">
              <span className="w-3 h-3 border-2 border-[var(--accent)]/30 border-t-[var(--accent)] rounded-full animate-spin" />
              Cleaning...
            </span>
          ) : (
            <>
              <span className="block text-[15px] mb-0.5">Cleanup Indexes</span>
              <span className="text-[11px] opacity-70">Remove orphaned index notes</span>
            </>
          )}
        </button>

        <button onClick={runCommunityDetection} disabled={!!loading} className={btnClass("community")}>
          {loading === "community" ? (
            <span className="inline-flex items-center gap-2">
              <span className="w-3 h-3 border-2 border-[var(--accent)]/30 border-t-[var(--accent)] rounded-full animate-spin" />
              Detecting...
            </span>
          ) : (
            <>
              <span className="block text-[15px] mb-0.5">Detect Communities</span>
              <span className="text-[11px] opacity-70">Cluster notes into topic groups</span>
            </>
          )}
        </button>
      </div>

      {/* Operation results */}
      {results.length > 0 && (
        <div className="mb-8 p-4 bg-[var(--surface)] border border-[var(--border)] rounded-lg">
          <h3 className="text-[13px] font-semibold text-[var(--heading)] mb-2">Results</h3>
          <ul className="space-y-1">
            {results.map((r, i) => (
              <li key={i} className="text-[13px] text-[var(--text-secondary)]">{r}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Repair result */}
      {repairResult && (
        <div className="mb-8 p-4 bg-[var(--surface)] border border-[var(--border)] rounded-lg">
          <h3 className="text-[13px] font-semibold text-[var(--heading)] mb-2">
            Repaired: {repairResult.title}
          </h3>
          {repairResult.changes.length > 0 ? (
            <ul className="space-y-1">
              {repairResult.changes.map((c, i) => (
                <li key={i} className="text-[13px] text-[var(--text-secondary)]">{c}</li>
              ))}
            </ul>
          ) : (
            <p className="text-[13px] text-[var(--muted)]">No changes needed</p>
          )}
          {repairResult.new_links.length > 0 && (
            <p className="text-[12px] text-[var(--accent)] mt-2">
              New links: {repairResult.new_links.join(", ")}
            </p>
          )}
        </div>
      )}

      {/* Community detection results */}
      {clusters.length > 0 && (
        <div className="mb-8">
          <Section title={`Topic Clusters (${clusters.length})`}>
            {clusters.map((c) => (
              <div key={c.id} className="py-2.5 border-b border-[var(--border-light)] last:border-0" style={{ paddingLeft: `${(c.level || 0) * 16 + 4}px` }}>
                <div className="flex items-center justify-between">
                  <span className="text-[13px] font-medium text-[var(--text)]">{c.label}</span>
                  <span className="text-[11px] text-[var(--muted)]">{c.member_count} notes</span>
                </div>
                {c.path && c.path !== c.label && (
                  <p className="text-[11px] text-[var(--muted)] mt-0.5">{c.path}</p>
                )}
                {c.summary && (
                  <p className="text-[12px] text-[var(--text-secondary)] mt-1">{c.summary}</p>
                )}
              </div>
            ))}
          </Section>
        </div>
      )}

      {/* Audit results */}
      {audit && (
        <div className="space-y-6">
          {/* Summary cards */}
          <div className="grid grid-cols-3 gap-3">
            <div className="p-4 bg-[var(--surface)] border border-[var(--border)] rounded-lg text-center">
              <div className="text-[24px] font-bold text-[var(--heading)]">{audit.total_notes}</div>
              <div className="text-[12px] text-[var(--muted)]">Total Notes</div>
            </div>
            <div className="p-4 bg-[var(--surface)] border border-[var(--border)] rounded-lg text-center">
              <div className="text-[24px] font-bold text-[var(--callout-amber)]">{audit.shallow_notes.length}</div>
              <div className="text-[12px] text-[var(--muted)]">Shallow</div>
            </div>
            <div className="p-4 bg-[var(--surface)] border border-[var(--border)] rounded-lg text-center">
              <div className="text-[24px] font-bold text-[var(--danger)]">{audit.orphan_notes.length}</div>
              <div className="text-[12px] text-[var(--muted)]">Orphans</div>
            </div>
          </div>

          {/* Missing frontmatter */}
          {audit.missing_frontmatter.length > 0 && (
            <Section title={`Missing Frontmatter (${audit.missing_frontmatter.length})`}>
              {audit.missing_frontmatter.map((n) => (
                <IssueRow
                  key={n.id}
                  title={n.title}
                  detail={n.issues.join(", ")}
                  noteId={n.id}
                  loading={loading}
                  onRepair={() => repairNote(n.id)}
                />
              ))}
            </Section>
          )}

          {/* Shallow notes */}
          {audit.shallow_notes.length > 0 && (
            <Section title={`Shallow Notes (${audit.shallow_notes.length})`}>
              {audit.shallow_notes.map((n) => (
                <IssueRow
                  key={n.id}
                  title={n.title}
                  detail={`${n.words} words`}
                  noteId={n.id}
                  loading={loading}
                  onRepair={() => repairNote(n.id)}
                />
              ))}
            </Section>
          )}

          {/* Orphan notes */}
          {audit.orphan_notes.length > 0 && (
            <Section title={`Orphan Notes (${audit.orphan_notes.length})`}>
              {audit.orphan_notes.map((n) => (
                <IssueRow
                  key={n.id}
                  title={n.title}
                  detail="No connections"
                  noteId={n.id}
                  loading={loading}
                  onRepair={() => repairNote(n.id)}
                />
              ))}
            </Section>
          )}

          {/* Broken links */}
          {audit.broken_links.length > 0 && (
            <Section title={`Broken Links (${audit.broken_links.length})`}>
              {audit.broken_links.map((b, i) => (
                <div key={i} className="flex items-center justify-between py-2 border-b border-[var(--border-light)] last:border-0">
                  <div>
                    <span className="text-[13px] text-[var(--text)]">{b.note_title}</span>
                    <span className="text-[12px] text-[var(--danger)] ml-2">→ [[{b.broken_link}]]</span>
                  </div>
                </div>
              ))}
            </Section>
          )}

          {/* Missing indexes */}
          {audit.missing_indexes.length > 0 && (
            <Section title={`Missing Indexes (${audit.missing_indexes.length})`}>
              {audit.missing_indexes.map((folder) => (
                <div key={folder} className="py-2 border-b border-[var(--border-light)] last:border-0">
                  <span className="text-[13px] text-[var(--text-secondary)]">{folder}</span>
                </div>
              ))}
              <button
                onClick={runReindex}
                disabled={!!loading}
                className="mt-3 px-3 py-1.5 text-[12px] bg-[var(--accent)] text-white rounded hover:bg-[var(--link)] transition-colors"
              >
                Create Missing Indexes
              </button>
            </Section>
          )}
        </div>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-[var(--surface)] border border-[var(--border)] rounded-lg overflow-hidden">
      <div className="px-4 py-2.5 border-b border-[var(--border)] bg-[var(--surface2)]">
        <h3 className="text-[13px] font-semibold text-[var(--heading)]">{title}</h3>
      </div>
      <div className="px-4 py-2">{children}</div>
    </div>
  );
}

function IssueRow({
  title,
  detail,
  noteId,
  loading,
  onRepair,
}: {
  title: string;
  detail: string;
  noteId: number;
  loading: string;
  onRepair: () => void;
}) {
  const isRepairing = loading === `repair-${noteId}`;
  return (
    <div className="flex items-center justify-between py-2 border-b border-[var(--border-light)] last:border-0">
      <div className="flex-1 min-w-0">
        <span className="text-[13px] text-[var(--text)]">{title}</span>
        <span className="text-[12px] text-[var(--muted)] ml-2">{detail}</span>
      </div>
      <button
        onClick={onRepair}
        disabled={!!loading}
        className="flex-shrink-0 ml-3 px-2.5 py-1 text-[11px] font-medium rounded border border-[var(--border)] text-[var(--text-secondary)] hover:border-[var(--accent-light)] hover:text-[var(--accent)] disabled:opacity-50 transition-all"
      >
        {isRepairing ? (
          <span className="w-3 h-3 border-2 border-[var(--accent)]/30 border-t-[var(--accent)] rounded-full animate-spin inline-block" />
        ) : (
          "Repair"
        )}
      </button>
    </div>
  );
}
