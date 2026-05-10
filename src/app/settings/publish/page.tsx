"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { useAuth } from "@/components/AuthProvider";

interface SpaceOption {
  id: number;
  name: string;
  slug: string;
}

interface Repo {
  external_id: string;
  full_name: string;
  resource_id: number | null;
  enabled: boolean;
}

interface Account {
  id: number;
  label: string;
}

interface PublishTarget {
  id: number;
  account_id: number;
  space_id: number;
  repo_full_name: string;
  wiki_branch: string;
  last_published_sha: string | null;
  last_published_at: string | null;
}

interface PublishResult {
  dry_run: boolean;
  space_id: number;
  pages_written: number;
  pages_deleted: number;
  orphan_links: Array<{ source_title: string; display_text: string; target_title: string }>;
  total_wikilinks_seen: number;
  orphan_rate: number;
  commit_sha: string | null;
  pushed: boolean;
  errors: string[];
  synced_at: string | null;
}

export default function PublishPage() {
  const { role, loading: authLoading } = useAuth();
  const [spaces, setSpaces] = useState<SpaceOption[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [targets, setTargets] = useState<Record<number, PublishTarget | null>>({});
  const [reposByAccount, setReposByAccount] = useState<Record<number, Repo[]>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [runningSpaceId, setRunningSpaceId] = useState<number | null>(null);
  const [results, setResults] = useState<Record<number, PublishResult>>({});

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [spacesRes, accountsRes] = await Promise.all([
        fetch("/api/spaces"),
        fetch("/api/integrations/github/accounts"),
      ]);
      if (!spacesRes.ok || !accountsRes.ok) {
        setError("Failed to load spaces or accounts.");
        return;
      }
      const spaceList: SpaceOption[] = await spacesRes.json();
      const acctList: Account[] = (await accountsRes.json()).map(
        (a: { id: number; label: string }) => ({ id: a.id, label: a.label }),
      );
      setSpaces(spaceList);
      setAccounts(acctList);

      const targetEntries = await Promise.all(
        spaceList.map(async (s) => {
          const r = await fetch(`/api/integrations/github/publish-targets/space/${s.id}`);
          if (!r.ok) return [s.id, null] as const;
          const body = await r.json();
          return [s.id, body as PublishTarget | null] as const;
        }),
      );
      setTargets(Object.fromEntries(targetEntries));

      const repoEntries = await Promise.all(
        acctList.map(async (a) => {
          const r = await fetch(`/api/integrations/github/accounts/${a.id}/repos`);
          if (!r.ok) return [a.id, [] as Repo[]] as const;
          return [a.id, (await r.json()) as Repo[]] as const;
        }),
      );
      setReposByAccount(Object.fromEntries(repoEntries));
    } catch (e) {
      setError(`Failed to load: ${String(e)}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!authLoading && role === "admin") refresh();
    else if (!authLoading) setLoading(false);
  }, [authLoading, role, refresh]);

  const saveTarget = async (
    spaceId: number,
    accountId: number,
    repoFullName: string,
  ) => {
    const res = await fetch(
      `/api/integrations/github/publish-targets/space/${spaceId}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          account_id: accountId,
          repo_full_name: repoFullName,
          wiki_branch: "master",
        }),
      },
    );
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      alert(`Save failed: ${body.detail || res.status}`);
      return;
    }
    await refresh();
  };

  const clearTarget = async (spaceId: number) => {
    if (!confirm("Remove publish target for this space?")) return;
    const res = await fetch(
      `/api/integrations/github/publish-targets/space/${spaceId}`,
      { method: "DELETE" },
    );
    if (!res.ok && res.status !== 404) {
      alert(`Delete failed: ${res.status}`);
      return;
    }
    await refresh();
  };

  const runPublish = async (spaceId: number, dryRun: boolean, force = false) => {
    setRunningSpaceId(spaceId);
    try {
      const res = await fetch("/api/integrations/github/publish", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ space_id: spaceId, dry_run: dryRun, force_overwrite: force }),
      });
      const body: PublishResult = await res.json();
      setResults((prev) => ({ ...prev, [spaceId]: body }));
      if (!dryRun && res.ok && body.pushed) await refresh();
    } catch (e) {
      alert(`Publish failed: ${String(e)}`);
    } finally {
      setRunningSpaceId(null);
    }
  };

  if (authLoading) return <div className="p-8 text-[var(--muted)]">Loading…</div>;
  if (role !== "admin") {
    return (
      <div className="max-w-[720px] mx-auto px-4 py-6 md:px-8 md:py-8">
        <h1 className="text-[24px] font-bold text-[var(--heading)] mb-4">Publish</h1>
        <p className="text-[var(--muted)]">Admin only.</p>
      </div>
    );
  }

  const sectionClass = "bg-[var(--surface)] rounded-lg p-5 border border-[var(--border)] mb-4";
  const selectClass =
    "px-3 py-1.5 text-[13px] bg-[var(--bg)] border border-[var(--border)] rounded text-[var(--text)] focus:outline-none focus:border-[var(--accent-light)]";
  const btnPrimary =
    "inline-flex items-center px-3 py-1.5 text-[13px] font-medium bg-[var(--accent)] text-white rounded hover:bg-[var(--accent-light)] transition-colors disabled:opacity-50";
  const btnSecondary =
    "inline-flex items-center px-3 py-1.5 text-[13px] bg-[var(--surface2)] text-[var(--text)] rounded border border-[var(--border)] hover:bg-[var(--border)] transition-colors disabled:opacity-50";

  return (
    <div className="max-w-[960px] mx-auto px-4 py-6 md:px-8 md:py-8">
      <div className="mb-4">
        <Link href="/settings" className="text-[13px] text-[var(--text-secondary)] hover:text-[var(--accent)]">
          ← Settings
        </Link>
      </div>
      <h1 className="text-[24px] font-bold text-[var(--heading)] mb-1">Publish to GitHub wiki</h1>
      <p className="text-[13px] text-[var(--text-secondary)] mb-5">
        Each space can publish its public notes to a linked repo&apos;s wiki. Admin-only notes are
        never pushed.
      </p>

      {error && <p className="text-[13px] text-[var(--danger)] mb-3">{error}</p>}

      {loading ? (
        <p className="text-[13px] text-[var(--muted)]">Loading…</p>
      ) : spaces.length === 0 ? (
        <p className="text-[13px] text-[var(--muted)]">No spaces yet.</p>
      ) : accounts.length === 0 ? (
        <p className="text-[13px] text-[var(--muted)]">
          Link a GitHub account first in <Link href="/settings/integrations" className="underline">Integrations</Link>.
        </p>
      ) : (
        spaces.map((s) => {
          const target = targets[s.id];
          const running = runningSpaceId === s.id;
          const result = results[s.id];
          return (
            <section key={s.id} className={sectionClass}>
              <div className="flex items-start justify-between gap-4 mb-3">
                <div>
                  <h2 className="text-[16px] font-semibold text-[var(--heading)]">{s.name}</h2>
                  <p className="text-[12px] text-[var(--text-secondary)]">slug: {s.slug}</p>
                </div>
                {target && (
                  <button onClick={() => clearTarget(s.id)} className={btnSecondary}>
                    Remove target
                  </button>
                )}
              </div>

              <TargetPicker
                spaceId={s.id}
                accounts={accounts}
                reposByAccount={reposByAccount}
                current={target}
                onSave={saveTarget}
              />

              {target && (
                <>
                  <div className="mt-3 text-[12px] text-[var(--text-secondary)]">
                    {target.last_published_at
                      ? `Last published ${new Date(target.last_published_at).toLocaleString()} (sha ${target.last_published_sha?.slice(0, 7)})`
                      : "Never published"}
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button disabled={running} onClick={() => runPublish(s.id, true)} className={btnSecondary}>
                      {running ? "Running…" : "Dry run"}
                    </button>
                    <button disabled={running} onClick={() => runPublish(s.id, false)} className={btnPrimary}>
                      Publish now
                    </button>
                    <button disabled={running} onClick={() => runPublish(s.id, false, true)} className={btnSecondary}>
                      Publish + force overwrite
                    </button>
                  </div>
                  {result && <PublishReport result={result} />}
                </>
              )}
            </section>
          );
        })
      )}
    </div>
  );
}

function TargetPicker({
  spaceId,
  accounts,
  reposByAccount,
  current,
  onSave,
}: {
  spaceId: number;
  accounts: Account[];
  reposByAccount: Record<number, Repo[]>;
  current: PublishTarget | null;
  onSave: (spaceId: number, accountId: number, repoFullName: string) => Promise<void>;
}) {
  const [accountId, setAccountId] = useState<number>(current?.account_id ?? accounts[0]?.id ?? 0);
  const [repoName, setRepoName] = useState<string>(current?.repo_full_name ?? "");
  const repos = reposByAccount[accountId] || [];

  useEffect(() => {
    if (current) {
      setAccountId(current.account_id);
      setRepoName(current.repo_full_name);
    }
  }, [current]);

  return (
    <div className="flex flex-wrap items-center gap-2">
      <select
        value={accountId}
        onChange={(e) => {
          const newId = Number(e.target.value);
          setAccountId(newId);
          setRepoName("");
        }}
        className="px-3 py-1.5 text-[13px] bg-[var(--bg)] border border-[var(--border)] rounded text-[var(--text)] focus:outline-none focus:border-[var(--accent-light)]"
      >
        {accounts.map((a) => (
          <option key={a.id} value={a.id}>{a.label}</option>
        ))}
      </select>
      <select
        value={repoName}
        onChange={(e) => setRepoName(e.target.value)}
        className="px-3 py-1.5 text-[13px] bg-[var(--bg)] border border-[var(--border)] rounded text-[var(--text)] focus:outline-none focus:border-[var(--accent-light)]"
      >
        <option value="">(pick a repo)</option>
        {repos.map((r) => (
          <option key={r.external_id} value={r.full_name}>{r.full_name}</option>
        ))}
      </select>
      <button
        disabled={!accountId || !repoName}
        onClick={() => onSave(spaceId, accountId, repoName)}
        className="px-3 py-1.5 text-[13px] font-medium bg-[var(--accent)] text-white rounded hover:bg-[var(--accent-light)] transition-colors disabled:opacity-50"
      >
        {current ? "Update target" : "Set target"}
      </button>
    </div>
  );
}

function PublishReport({ result }: { result: PublishResult }) {
  return (
    <div className="mt-3 p-3 rounded border border-[var(--border)] bg-[var(--bg)] text-[12px]">
      <div className="font-medium mb-1">
        {result.dry_run ? "Dry run" : result.pushed ? "Published" : "No changes"}
      </div>
      <div className="text-[var(--text-secondary)]">
        pages: {result.pages_written} written
        {result.pages_deleted ? ` · ${result.pages_deleted} deleted` : ""}
        {" · "}wikilinks: {result.total_wikilinks_seen} total, {result.orphan_links.length} orphaned ({(result.orphan_rate * 100).toFixed(0)}%)
        {result.commit_sha ? ` · sha ${result.commit_sha.slice(0, 7)}` : ""}
      </div>
      {result.errors.length > 0 && (
        <ul className="mt-2 text-[var(--danger)] list-disc list-inside">
          {result.errors.map((e, i) => <li key={i}>{e}</li>)}
        </ul>
      )}
      {result.orphan_links.length > 0 && result.orphan_links.length <= 10 && (
        <details className="mt-2 text-[var(--text-secondary)]">
          <summary className="cursor-pointer">{result.orphan_links.length} orphaned link(s)</summary>
          <ul className="mt-1 list-disc list-inside">
            {result.orphan_links.map((o, i) => (
              <li key={i}>
                <code className="text-[11px]">{o.source_title}</code> → <code>{o.target_title}</code>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
