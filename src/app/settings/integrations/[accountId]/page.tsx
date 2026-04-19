"use client";

import { use, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";

import { useAuth } from "@/components/AuthProvider";

interface Repo {
  external_id: string;
  full_name: string;
  owner: string;
  name: string;
  default_branch: string | null;
  private: boolean;
  resource_id: number | null;
  enabled: boolean;
  space_id: number | null;
  issues_enabled: boolean;
  prs_enabled: boolean;
  wiki_enabled: boolean;
  last_synced_at: string | null;
}

interface SpaceOption {
  id: number;
  name: string;
  slug: string;
}

export default function AccountReposPage({
  params,
}: {
  params: Promise<{ accountId: string }>;
}) {
  const { accountId } = use(params);
  const { role, loading: authLoading } = useAuth();

  const [repos, setRepos] = useState<Repo[]>([]);
  const [spaces, setSpaces] = useState<SpaceOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<Date | null>(null);
  const [filter, setFilter] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [reposRes, spacesRes] = await Promise.all([
        fetch(`/api/integrations/github/accounts/${accountId}/repos`),
        fetch("/api/spaces"),
      ]);
      if (reposRes.status === 403) {
        setError("Admin only.");
        return;
      }
      if (!reposRes.ok) {
        setError(`Failed to load repos (${reposRes.status})`);
        return;
      }
      setRepos(await reposRes.json());
      if (spacesRes.ok) setSpaces(await spacesRes.json());
    } catch (e) {
      setError(`Failed to load: ${String(e)}`);
    } finally {
      setLoading(false);
    }
  }, [accountId]);

  useEffect(() => {
    if (!authLoading && role === "admin") {
      refresh();
    } else if (!authLoading) {
      setLoading(false);
    }
  }, [authLoading, role, refresh]);

  const patchRepo = (ext_id: string, patch: Partial<Repo>) => {
    setRepos((prev) =>
      prev.map((r) => (r.external_id === ext_id ? { ...r, ...patch } : r)),
    );
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const payload = {
        repos: repos
          .filter((r) => r.enabled || r.resource_id !== null)
          .map((r) => ({
            external_id: r.external_id,
            full_name: r.full_name,
            default_branch: r.default_branch,
            private: r.private,
            enabled: r.enabled,
            space_id: r.space_id,
            issues_enabled: r.issues_enabled,
            prs_enabled: r.prs_enabled,
            wiki_enabled: r.wiki_enabled,
          })),
      };
      const res = await fetch(
        `/api/integrations/github/accounts/${accountId}/resources`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        },
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError(body.detail || `Save failed (${res.status})`);
        return;
      }
      setSavedAt(new Date());
      await refresh();
    } catch (e) {
      setError(`Save failed: ${String(e)}`);
    } finally {
      setSaving(false);
    }
  };

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return repos;
    return repos.filter((r) => r.full_name.toLowerCase().includes(q));
  }, [repos, filter]);

  if (authLoading) return <div className="p-8 text-[var(--muted)]">Loading…</div>;
  if (role !== "admin") {
    return (
      <div className="max-w-[720px] mx-auto px-4 py-6 md:px-8 md:py-8">
        <h1 className="text-[24px] font-bold text-[var(--heading)] mb-4">Account repos</h1>
        <p className="text-[var(--muted)]">Admin only.</p>
      </div>
    );
  }

  const btnPrimary =
    "inline-flex items-center px-4 py-2 text-[14px] font-medium bg-[var(--accent)] text-white rounded hover:bg-[var(--accent-light)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed";
  const inputClass =
    "px-3 py-1.5 text-[13px] bg-[var(--bg)] border border-[var(--border)] rounded text-[var(--text)] focus:outline-none focus:border-[var(--accent-light)]";
  const selectClass = inputClass + " w-full";

  return (
    <div className="max-w-[960px] mx-auto px-4 py-6 md:px-8 md:py-8">
      <div className="mb-4">
        <Link
          href="/settings/integrations"
          className="text-[13px] text-[var(--text-secondary)] hover:text-[var(--accent)]"
        >
          ← Integrations
        </Link>
      </div>

      <h1 className="text-[24px] font-bold text-[var(--heading)] mb-1">GitHub repos</h1>
      <p className="text-[13px] text-[var(--text-secondary)] mb-5">
        Pick which repos to track. Each tracked repo can ingest issues, PRs, and wiki pages into the
        chosen space.
      </p>

      <div className="flex items-center gap-3 mb-4">
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter repos…"
          className={inputClass + " flex-1 max-w-sm"}
        />
        <button onClick={handleSave} disabled={saving || loading} className={btnPrimary}>
          {saving ? "Saving…" : "Save"}
        </button>
        {savedAt && (
          <span className="text-[12px] text-[var(--callout-green)]">
            Saved at {savedAt.toLocaleTimeString()}
          </span>
        )}
      </div>

      {error && <p className="text-[13px] text-[var(--danger)] mb-3">{error}</p>}

      {loading ? (
        <p className="text-[13px] text-[var(--muted)]">Loading repos…</p>
      ) : filtered.length === 0 ? (
        <p className="text-[13px] text-[var(--muted)]">
          {repos.length === 0 ? "The installation has no accessible repos." : "No repos match filter."}
        </p>
      ) : (
        <div className="bg-[var(--surface)] rounded-lg border border-[var(--border)] overflow-hidden">
          <table className="w-full text-[13px]">
            <thead className="bg-[var(--surface2)] text-[var(--text-secondary)]">
              <tr>
                <th className="px-3 py-2 text-left font-medium">Track</th>
                <th className="px-3 py-2 text-left font-medium">Repo</th>
                <th className="px-3 py-2 text-left font-medium">Space</th>
                <th className="px-3 py-2 text-center font-medium">Issues</th>
                <th className="px-3 py-2 text-center font-medium">PRs</th>
                <th className="px-3 py-2 text-center font-medium">Wiki</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--border)]">
              {filtered.map((r) => (
                <tr key={r.external_id}>
                  <td className="px-3 py-2">
                    <input
                      type="checkbox"
                      checked={r.enabled}
                      onChange={(e) => patchRepo(r.external_id, { enabled: e.target.checked })}
                    />
                  </td>
                  <td className="px-3 py-2">
                    <div className="font-medium text-[var(--text)]">{r.full_name}</div>
                    <div className="text-[11px] text-[var(--text-secondary)]">
                      {r.private ? "private" : "public"}
                      {r.default_branch ? ` · ${r.default_branch}` : null}
                      {r.last_synced_at ? ` · synced ${new Date(r.last_synced_at).toLocaleDateString()}` : null}
                    </div>
                  </td>
                  <td className="px-3 py-2">
                    <select
                      value={r.space_id ?? ""}
                      onChange={(e) =>
                        patchRepo(r.external_id, {
                          space_id: e.target.value ? Number(e.target.value) : null,
                        })
                      }
                      disabled={!r.enabled}
                      className={selectClass}
                    >
                      <option value="">(select)</option>
                      {spaces.map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.name}
                        </option>
                      ))}
                    </select>
                  </td>
                  {(["issues_enabled", "prs_enabled", "wiki_enabled"] as const).map((k) => (
                    <td key={k} className="px-3 py-2 text-center">
                      <input
                        type="checkbox"
                        checked={r[k]}
                        disabled={!r.enabled}
                        onChange={(e) => patchRepo(r.external_id, { [k]: e.target.checked })}
                      />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
