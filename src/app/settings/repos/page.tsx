"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";

import { useAuth } from "@/components/AuthProvider";
import { useSpace } from "@/contexts/SpaceContext";
import { apiUrl } from "@/lib/api";

interface VaultRepo {
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
  code_enabled: boolean;
  code_extraction_profile_id: number | null;
  code_include_exts: string[];
  last_synced_at: string | null;
  account_id: number;
  account_label: string;
}

interface ExtractionProfile {
  id: number;
  name: string;
  extensions: string[];
  space_id: number;
  space_name: string | null;
}

interface ConnectedAccount {
  id: number;
  label: string;
}

interface AvailableRepo {
  external_id: string;
  full_name: string;
  owner: string;
  name: string;
  default_branch: string | null;
  private: boolean;
  resource_id: number | null;
  space_id: number | null;
}

interface SyncResultData {
  detail?: string;
  issues_ingested?: number;
  issues_updated?: number;
  prs_ingested?: number;
  prs_updated?: number;
  wiki_ingested?: number;
  wiki_updated?: number;
  wiki_deleted?: number;
  code_ingested?: number;
  code_updated?: number;
  code_deleted?: number;
  code_skipped_too_large?: number;
  errors?: string[];
}

export default function VaultReposPage() {
  const { role, loading: authLoading } = useAuth();
  const { spaceSlug, spaces } = useSpace();
  const currentSpace = useMemo(
    () => spaces.find((s) => s.slug === spaceSlug) ?? null,
    [spaces, spaceSlug],
  );

  const [repos, setRepos] = useState<VaultRepo[]>([]);
  const [accounts, setAccounts] = useState<ConnectedAccount[]>([]);
  const [profiles, setProfiles] = useState<ExtractionProfile[]>([]);
  const [availableByAccount, setAvailableByAccount] = useState<
    Record<number, AvailableRepo[]>
  >({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [syncingId, setSyncingId] = useState<number | null>(null);
  const [syncResults, setSyncResults] = useState<Record<number, string>>({});

  const trackedExternalIdsByAccount = useMemo(() => {
    const m: Record<number, Set<string>> = {};
    for (const r of repos) {
      if (!m[r.account_id]) m[r.account_id] = new Set();
      m[r.account_id].add(r.external_id);
    }
    return m;
  }, [repos]);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [reposRes, accountsRes, profilesRes] = await Promise.all([
        fetch(apiUrl("/api/integrations/github/repos", spaceSlug)),
        fetch("/api/integrations/github/accounts"),
        fetch("/api/extraction-profiles?all=true"),
      ]);
      if (reposRes.status === 403) {
        setError("Admin only.");
        return;
      }
      if (!reposRes.ok) {
        setError(`Failed to load tracked repos (${reposRes.status})`);
        return;
      }
      setRepos(await reposRes.json());
      if (accountsRes.ok) {
        const accts: ConnectedAccount[] = await accountsRes.json();
        setAccounts(accts.map((a) => ({ id: a.id, label: a.label })));
        const entries = await Promise.all(
          accts.map(async (a) => {
            const r = await fetch(`/api/integrations/github/accounts/${a.id}/repos`);
            if (!r.ok) return [a.id, [] as AvailableRepo[]] as const;
            return [a.id, (await r.json()) as AvailableRepo[]] as const;
          }),
        );
        setAvailableByAccount(Object.fromEntries(entries));
      }
      if (profilesRes.ok) setProfiles(await profilesRes.json());
    } catch (e) {
      setError(`Failed to load: ${String(e)}`);
    } finally {
      setLoading(false);
    }
  }, [spaceSlug]);

  useEffect(() => {
    if (!authLoading && role === "admin") refresh();
    else if (!authLoading) setLoading(false);
  }, [authLoading, role, refresh]);

  const patchRepo = async (resourceId: number, patch: Partial<VaultRepo>) => {
    const body: Record<string, unknown> = {};
    if ("enabled" in patch) body.enabled = patch.enabled;
    if ("issues_enabled" in patch) body.issues_enabled = patch.issues_enabled;
    if ("prs_enabled" in patch) body.prs_enabled = patch.prs_enabled;
    if ("wiki_enabled" in patch) body.wiki_enabled = patch.wiki_enabled;
    if ("code_enabled" in patch) body.code_enabled = patch.code_enabled;
    if ("code_extraction_profile_id" in patch) {
      body.code_extraction_profile_id = patch.code_extraction_profile_id;
    }
    if ("code_include_exts" in patch) body.code_include_exts = patch.code_include_exts;

    setRepos((prev) =>
      prev.map((r) =>
        r.resource_id === resourceId ? { ...r, ...patch } : r,
      ),
    );
    const res = await fetch(`/api/integrations/github/resources/${resourceId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const msg = await res.json().catch(() => ({}));
      setError(`Save failed: ${msg.detail || res.status}`);
      await refresh();
      return;
    }
    const updated: VaultRepo = await res.json();
    setRepos((prev) =>
      prev.map((r) => (r.resource_id === resourceId ? updated : r)),
    );
  };

  const handleSync = async (resourceId: number) => {
    setSyncingId(resourceId);
    setSyncResults((prev) => ({ ...prev, [resourceId]: "Syncing…" }));
    try {
      const res = await fetch(
        `/api/integrations/github/resources/${resourceId}/sync`,
        { method: "POST" },
      );
      // 5xx from FastAPI returns text/plain "Internal Server Error", not JSON.
      // Read once as text, then try to parse — so we surface the real message
      // instead of SyntaxError from res.json().
      const raw = await res.text();
      let data: SyncResultData = {};
      try {
        data = raw ? (JSON.parse(raw) as SyncResultData) : {};
      } catch {
        // Non-JSON body (e.g. plaintext 500) — expose it directly.
        setSyncResults((prev) => ({
          ...prev,
          [resourceId]: `Error ${res.status}: ${raw.slice(0, 200) || "no body"}`,
        }));
        return;
      }
      if (!res.ok) {
        setSyncResults((prev) => ({
          ...prev,
          [resourceId]: `Error: ${data.detail || res.status}`,
        }));
        return;
      }
      const parts: string[] = [];
      if (data.issues_ingested || data.issues_updated)
        parts.push(`${data.issues_ingested} new / ${data.issues_updated} updated issues`);
      if (data.prs_ingested || data.prs_updated)
        parts.push(`${data.prs_ingested} new / ${data.prs_updated} updated PRs`);
      if (data.wiki_ingested || data.wiki_updated || data.wiki_deleted)
        parts.push(
          `wiki: ${data.wiki_ingested}+/${data.wiki_updated}~/${data.wiki_deleted}-`,
        );
      if (data.code_ingested || data.code_updated || data.code_deleted)
        parts.push(
          `code: ${data.code_ingested}+/${data.code_updated}~/${data.code_deleted}- (processing in background)`,
        );
      if (data.code_skipped_too_large)
        parts.push(`${data.code_skipped_too_large} code files over size limit`);
      if (data.errors?.length) {
        const shown = data.errors.slice(0, 3).join(" | ");
        const extra = data.errors.length > 3 ? ` (+${data.errors.length - 3} more)` : "";
        parts.push(`error: ${shown}${extra}`);
      }
      setSyncResults((prev) => ({
        ...prev,
        [resourceId]: parts.length ? parts.join(" · ") : "No changes",
      }));
      await refresh();
    } catch (e) {
      setSyncResults((prev) => ({ ...prev, [resourceId]: `Error: ${String(e)}` }));
    } finally {
      setSyncingId(null);
    }
  };

  const handleRemove = async (resourceId: number, fullName: string) => {
    if (!confirm(`Stop tracking ${fullName}? Generated notes stay in place.`)) return;
    const res = await fetch(`/api/integrations/github/resources/${resourceId}`, {
      method: "DELETE",
    });
    if (!res.ok && res.status !== 404) {
      alert(`Remove failed (${res.status})`);
      return;
    }
    await refresh();
  };

  if (authLoading) return <div className="p-8 text-[var(--muted)]">Loading…</div>;
  if (role !== "admin") {
    return (
      <div className="max-w-[720px] mx-auto px-4 py-6 md:px-8 md:py-8">
        <h1 className="text-[24px] font-bold text-[var(--heading)] mb-4">Vault repos</h1>
        <p className="text-[var(--muted)]">Admin only.</p>
      </div>
    );
  }

  return (
    <div className="max-w-[960px] mx-auto px-4 py-6 md:px-8 md:py-8">
      <div className="mb-4">
        <Link
          href="/settings"
          className="text-[13px] text-[var(--text-secondary)] hover:text-[var(--accent)]"
        >
          ← Settings
        </Link>
      </div>

      <h1 className="text-[24px] font-bold text-[var(--heading)] mb-1">
        GitHub repos in {currentSpace?.name ?? spaceSlug}
      </h1>
      <p className="text-[13px] text-[var(--text-secondary)] mb-5">
        Repos tracked into this vault. Switch vaults via the sidebar to manage
        tracking elsewhere. Connect accounts in{" "}
        <Link href="/settings/integrations" className="underline">
          Integrations
        </Link>
        .
      </p>

      {error && <p className="text-[13px] text-[var(--danger)] mb-3">{error}</p>}

      {loading ? (
        <p className="text-[13px] text-[var(--muted)]">Loading…</p>
      ) : accounts.length === 0 ? (
        <p className="text-[13px] text-[var(--muted)]">
          No GitHub accounts connected yet. Connect one in{" "}
          <Link href="/settings/integrations" className="underline">
            Integrations
          </Link>{" "}
          first.
        </p>
      ) : (
        <>
          <TrackedReposList
            repos={repos}
            profiles={profiles}
            syncingId={syncingId}
            syncResults={syncResults}
            onPatch={patchRepo}
            onSync={handleSync}
            onRemove={handleRemove}
          />
          <TrackNewRepo
            accounts={accounts}
            availableByAccount={availableByAccount}
            trackedExternalIdsByAccount={trackedExternalIdsByAccount}
            spaceSlug={spaceSlug}
            onTracked={refresh}
          />
        </>
      )}
    </div>
  );
}

function TrackedReposList({
  repos,
  profiles,
  syncingId,
  syncResults,
  onPatch,
  onSync,
  onRemove,
}: {
  repos: VaultRepo[];
  profiles: ExtractionProfile[];
  syncingId: number | null;
  syncResults: Record<number, string>;
  onPatch: (id: number, patch: Partial<VaultRepo>) => Promise<void>;
  onSync: (id: number) => Promise<void>;
  onRemove: (id: number, fullName: string) => Promise<void>;
}) {
  if (repos.length === 0) {
    return (
      <section className="bg-[var(--surface)] rounded-lg p-5 border border-[var(--border)] mb-5">
        <p className="text-[13px] text-[var(--muted)]">
          No repos tracked here yet. Pick one below to start.
        </p>
      </section>
    );
  }
  return (
    <section className="mb-6 space-y-3">
      {repos.map((r) => (
        <RepoCard
          key={r.resource_id ?? r.external_id}
          repo={r}
          profiles={profiles}
          syncing={syncingId === r.resource_id}
          syncMessage={r.resource_id ? syncResults[r.resource_id] : undefined}
          onPatch={onPatch}
          onSync={onSync}
          onRemove={onRemove}
        />
      ))}
    </section>
  );
}

function RepoCard({
  repo,
  profiles,
  syncing,
  syncMessage,
  onPatch,
  onSync,
  onRemove,
}: {
  repo: VaultRepo;
  profiles: ExtractionProfile[];
  syncing: boolean;
  syncMessage: string | undefined;
  onPatch: (id: number, patch: Partial<VaultRepo>) => Promise<void>;
  onSync: (id: number) => Promise<void>;
  onRemove: (id: number, fullName: string) => Promise<void>;
}) {
  const resourceId = repo.resource_id!;
  const codeNeedsExts =
    repo.code_enabled && repo.code_include_exts.length === 0;
  const hasAnyFeature =
    repo.issues_enabled || repo.prs_enabled || repo.wiki_enabled || repo.code_enabled;
  const canSync = repo.enabled && hasAnyFeature && !codeNeedsExts;

  const inputClass =
    "px-2 py-1 text-[12px] bg-[var(--bg)] border border-[var(--border)] rounded text-[var(--text)] focus:outline-none focus:border-[var(--accent-light)]";

  return (
    <div className="bg-[var(--surface)] rounded-lg border border-[var(--border)] p-4">
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <label className="inline-flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={repo.enabled}
                onChange={(e) => onPatch(resourceId, { enabled: e.target.checked })}
              />
              <span className="text-[11px] text-[var(--text-secondary)]">enabled</span>
            </label>
            <span className="text-[14px] font-semibold text-[var(--text)] truncate">
              {repo.full_name}
            </span>
          </div>
          <div className="text-[11px] text-[var(--text-secondary)] mt-0.5">
            {repo.account_label} · {repo.private ? "private" : "public"}
            {repo.default_branch ? ` · ${repo.default_branch}` : ""}
            {repo.last_synced_at
              ? ` · synced ${new Date(repo.last_synced_at).toLocaleString()}`
              : " · never synced"}
          </div>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => onSync(resourceId)}
            disabled={syncing || !canSync}
            className="px-3 py-1.5 text-[12px] bg-[var(--accent)] text-white rounded hover:bg-[var(--accent-light)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {syncing ? "Syncing…" : "Sync"}
          </button>
          <button
            onClick={() => onRemove(resourceId, repo.full_name)}
            className="px-3 py-1.5 text-[12px] bg-transparent text-[var(--danger)] rounded border border-[var(--border)] hover:bg-[var(--surface2)] transition-colors"
          >
            Remove
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-[12px]">
        <div className="flex flex-wrap items-center gap-3">
          {(
            [
              ["issues_enabled", "Issues"],
              ["prs_enabled", "PRs"],
              ["wiki_enabled", "Wiki"],
              ["code_enabled", "Code"],
            ] as const
          ).map(([k, label]) => (
            <label key={k} className="inline-flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={repo[k]}
                disabled={!repo.enabled}
                onChange={(e) => onPatch(resourceId, { [k]: e.target.checked } as Partial<VaultRepo>)}
              />
              <span>{label}</span>
            </label>
          ))}
        </div>
        {repo.code_enabled && (
          <div className="flex flex-col gap-1.5">
            <div className="flex flex-wrap items-center gap-2">
              <label className="text-[var(--text-secondary)]">Profile</label>
              <select
                value={repo.code_extraction_profile_id ?? ""}
                disabled={!repo.enabled}
                onChange={(e) => {
                  const nextId = e.target.value ? Number(e.target.value) : null;
                  const profile = profiles.find((p) => p.id === nextId) ?? null;
                  const patch: Partial<VaultRepo> = {
                    code_extraction_profile_id: nextId,
                  };
                  // Auto-fill extensions from the picked profile when the repo
                  // has none set yet — removes the "forgot to type py" footgun.
                  if (
                    profile &&
                    profile.extensions.length > 0 &&
                    repo.code_include_exts.length === 0
                  ) {
                    patch.code_include_exts = profile.extensions.map((e) =>
                      e.replace(/^\./, "").toLowerCase(),
                    );
                  }
                  onPatch(resourceId, patch);
                }}
                className={inputClass}
              >
                <option value="">(auto by ext)</option>
                {profiles.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.space_name ? `${p.name} — ${p.space_name}` : p.name}
                  </option>
                ))}
              </select>
              <IncludeExtsInput
                value={repo.code_include_exts}
                disabled={!repo.enabled}
                onCommit={(next) =>
                  onPatch(resourceId, { code_include_exts: next })
                }
                className={inputClass + " flex-1 min-w-[140px]"}
              />
            </div>
            {codeNeedsExts && (
              <div className="text-[11px] text-[var(--danger)]">
                Add at least one extension (e.g. <code>py</code>) or pick a profile
                — Sync is disabled until then.
              </div>
            )}
          </div>
        )}
      </div>

      {syncMessage && (
        <div className="mt-3 text-[11px] text-[var(--text-secondary)]">{syncMessage}</div>
      )}
    </div>
  );
}

function IncludeExtsInput({
  value,
  disabled,
  className,
  onCommit,
}: {
  value: string[];
  disabled: boolean;
  className: string;
  onCommit: (next: string[]) => void;
}) {
  const [draft, setDraft] = useState(value.join(", "));
  useEffect(() => {
    setDraft(value.join(", "));
  }, [value]);
  return (
    <input
      type="text"
      value={draft}
      disabled={disabled}
      placeholder="py, ts, js"
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        const parsed = draft
          .split(",")
          .map((s) => s.trim().replace(/^\./, "").toLowerCase())
          .filter(Boolean);
        const before = value.slice().sort().join(",");
        const after = parsed.slice().sort().join(",");
        if (before !== after) onCommit(parsed);
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
      }}
      title="Comma-separated file extensions (commits on blur / Enter)"
      className={className}
    />
  );
}

function TrackNewRepo({
  accounts,
  availableByAccount,
  trackedExternalIdsByAccount,
  spaceSlug,
  onTracked,
}: {
  accounts: ConnectedAccount[];
  availableByAccount: Record<number, AvailableRepo[]>;
  trackedExternalIdsByAccount: Record<number, Set<string>>;
  spaceSlug: string;
  onTracked: () => Promise<void>;
}) {
  const [accountId, setAccountId] = useState<number>(accounts[0]?.id ?? 0);
  const [externalId, setExternalId] = useState("");
  const [filter, setFilter] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!accountId && accounts.length) setAccountId(accounts[0].id);
  }, [accounts, accountId]);

  const available = availableByAccount[accountId] || [];
  const trackedHere = trackedExternalIdsByAccount[accountId] || new Set();
  const candidates = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return available
      .filter((r) => !trackedHere.has(r.external_id))
      // A non-null resource_id means it's already tracked by another vault —
      // admin must remove it there before re-tracking here.
      .filter((r) => r.resource_id === null)
      .filter((r) => (q ? r.full_name.toLowerCase().includes(q) : true));
  }, [available, trackedHere, filter]);

  const selected = candidates.find((r) => r.external_id === externalId) ?? null;

  const handleTrack = async () => {
    if (!selected) return;
    setSaving(true);
    setErr(null);
    try {
      const res = await fetch(
        apiUrl("/api/integrations/github/repos/track", spaceSlug),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            account_id: accountId,
            external_id: selected.external_id,
            full_name: selected.full_name,
            default_branch: selected.default_branch,
            private: selected.private,
          }),
        },
      );
      if (!res.ok) {
        const msg = await res.json().catch(() => ({}));
        setErr(msg.detail || `Track failed (${res.status})`);
        return;
      }
      setExternalId("");
      setFilter("");
      await onTracked();
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="bg-[var(--surface)] rounded-lg p-5 border border-[var(--border)]">
      <h2 className="text-[15px] font-semibold text-[var(--heading)] mb-1">
        Track another repo
      </h2>
      <p className="text-[12px] text-[var(--text-secondary)] mb-3">
        Pick from any connected account. Feature flags default to issues + PRs —
        tune them on the card after tracking. A repo can target one vault at a
        time per install; repos already tracked elsewhere won&apos;t appear here.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <select
          value={accountId}
          onChange={(e) => {
            setAccountId(Number(e.target.value));
            setExternalId("");
            setFilter("");
          }}
          className="px-2 py-1.5 text-[13px] bg-[var(--bg)] border border-[var(--border)] rounded text-[var(--text)] focus:outline-none focus:border-[var(--accent-light)]"
        >
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.label}
            </option>
          ))}
        </select>
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter repos…"
          className="px-2 py-1.5 text-[13px] bg-[var(--bg)] border border-[var(--border)] rounded text-[var(--text)] flex-1 min-w-[140px] max-w-sm focus:outline-none focus:border-[var(--accent-light)]"
        />
        <select
          value={externalId}
          onChange={(e) => setExternalId(e.target.value)}
          className="px-2 py-1.5 text-[13px] bg-[var(--bg)] border border-[var(--border)] rounded text-[var(--text)] flex-1 min-w-[200px] focus:outline-none focus:border-[var(--accent-light)]"
        >
          <option value="">({candidates.length} available)</option>
          {candidates.map((r) => (
            <option key={r.external_id} value={r.external_id}>
              {r.full_name}
            </option>
          ))}
        </select>
        <button
          disabled={!selected || saving}
          onClick={handleTrack}
          className="px-3 py-1.5 text-[13px] font-medium bg-[var(--accent)] text-white rounded hover:bg-[var(--accent-light)] transition-colors disabled:opacity-50"
        >
          {saving ? "Tracking…" : "Track"}
        </button>
      </div>
      {err && <p className="text-[12px] text-[var(--danger)] mt-2">{err}</p>}
    </section>
  );
}
