"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";

import { useAuth } from "@/components/AuthProvider";

interface ConnectedAccount {
  id: number;
  integration_type: string;
  label: string;
  external_account_id: string | null;
  created_at: string;
  metadata: {
    installation_id?: number;
    account_type?: string;
    repository_selection?: string;
    permissions?: Record<string, string>;
  };
}

function ConnectedBanner() {
  const searchParams = useSearchParams();
  const justConnected = searchParams.get("connected");
  if (justConnected !== "github") return null;
  return (
    <div className="mb-5 p-3 rounded border border-[var(--callout-green)] text-[13px] text-[var(--callout-green)]">
      GitHub account connected.
    </div>
  );
}

function IntegrationsBody() {
  const [accounts, setAccounts] = useState<ConnectedAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/integrations/github/accounts");
      if (res.status === 403) {
        setError("Admin only.");
        setAccounts([]);
        return;
      }
      if (!res.ok) {
        setError(`Failed to load accounts (${res.status})`);
        return;
      }
      setAccounts(await res.json());
    } catch (e) {
      setError(`Failed to load accounts: ${String(e)}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const handleDisconnect = async (id: number) => {
    if (!confirm("Disconnect this account? Any future sync will stop.")) return;
    const res = await fetch(`/api/integrations/github/accounts/${id}`, { method: "DELETE" });
    if (res.ok) {
      setAccounts((prev) => prev.filter((a) => a.id !== id));
    } else {
      alert(`Failed to disconnect (${res.status})`);
    }
  };

  const sectionClass = "bg-[var(--surface)] rounded-lg p-5 border border-[var(--border)]";
  const btnPrimary =
    "inline-flex items-center px-4 py-2 text-[14px] font-medium bg-[var(--accent)] text-white rounded hover:bg-[var(--accent-light)] transition-colors";
  const btnDanger =
    "px-3 py-1.5 text-[13px] bg-transparent text-[var(--danger)] rounded border border-[var(--border)] hover:bg-[var(--surface2)] transition-colors";

  return (
    <>
      <Suspense fallback={null}>
        <ConnectedBanner />
      </Suspense>

      <section className={sectionClass}>
        <div className="flex items-start justify-between gap-4 mb-4">
          <div>
            <h2 className="text-[16px] font-semibold text-[var(--heading)]">GitHub</h2>
            <p className="text-[13px] text-[var(--text-secondary)] mt-1">
              Link a GitHub account so issues, PRs, and wikis from selected repos appear as notes.
            </p>
          </div>
          <a href="/api/integrations/github/install" className={btnPrimary}>
            Connect GitHub
          </a>
        </div>

        {error && <p className="text-[13px] text-[var(--danger)] mb-3">{error}</p>}

        {loading ? (
          <p className="text-[13px] text-[var(--muted)]">Loading…</p>
        ) : accounts.length === 0 ? (
          <p className="text-[13px] text-[var(--muted)]">No accounts linked yet.</p>
        ) : (
          <ul className="divide-y divide-[var(--border)]">
            {accounts.map((acc) => (
              <li key={acc.id} className="py-3 flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <div className="text-[14px] font-medium text-[var(--text)] truncate">{acc.label}</div>
                  <div className="text-[12px] text-[var(--text-secondary)] mt-0.5">
                    Connected {new Date(acc.created_at).toLocaleDateString()}
                    {acc.metadata?.repository_selection ? (
                      <> · {acc.metadata.repository_selection === "all" ? "all repos" : "selected repos"}</>
                    ) : null}
                  </div>
                </div>
                <Link
                  href={`/settings/integrations/${acc.id}`}
                  className="px-3 py-1.5 text-[13px] bg-[var(--surface2)] text-[var(--text)] rounded border border-[var(--border)] hover:bg-[var(--border)] transition-colors"
                >
                  Manage repos
                </Link>
                <button onClick={() => handleDisconnect(acc.id)} className={btnDanger}>
                  Disconnect
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

export default function IntegrationsPage() {
  const { role, loading: authLoading } = useAuth();

  if (authLoading) {
    return <div className="p-8 text-[var(--muted)]">Loading…</div>;
  }

  if (role !== "admin") {
    return (
      <div className="max-w-[620px] mx-auto px-4 py-6 md:px-8 md:py-8">
        <h1 className="text-[24px] font-bold text-[var(--heading)] mb-4">Integrations</h1>
        <p className="text-[var(--muted)]">Admin only.</p>
      </div>
    );
  }

  return (
    <div className="max-w-[720px] mx-auto px-4 py-6 md:px-8 md:py-8">
      <h1 className="text-[24px] font-bold text-[var(--heading)] mb-6">Integrations</h1>
      <IntegrationsBody />
    </div>
  );
}
