"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/components/AuthProvider";

export default function LoginPage() {
  const [mode, setMode] = useState<"admin" | "user">("user");
  const [password, setPassword] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const router = useRouter();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

    const success = await login(
      mode,
      mode === "admin" ? password : undefined,
      mode === "user" ? apiKey || undefined : undefined,
    );

    setLoading(false);
    if (success) {
      router.push("/");
    } else {
      setError(mode === "admin" ? "Invalid password" : "Login failed");
    }
  }

  function handleGuest() {
    login("user");
    router.push("/");
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-[var(--bg)]">
      <div className="w-full max-w-[380px] px-6">
        <h1 className="text-[24px] font-bold text-[var(--heading)] mb-2 text-center">
          Second Brain
        </h1>
        <p className="text-[var(--text-secondary)] text-[13px] text-center mb-8">
          Knowledge base login
        </p>

        {/* Role tabs */}
        <div className="flex gap-1 p-1 bg-[var(--surface)] rounded-lg mb-6 border border-[var(--border-light)]">
          <button
            onClick={() => setMode("user")}
            className={`flex-1 py-2 text-[13px] font-medium rounded-md transition-all ${
              mode === "user"
                ? "bg-[var(--bg)] text-[var(--heading)] shadow-sm"
                : "text-[var(--text-secondary)]"
            }`}
          >
            Reader
          </button>
          <button
            onClick={() => setMode("admin")}
            className={`flex-1 py-2 text-[13px] font-medium rounded-md transition-all ${
              mode === "admin"
                ? "bg-[var(--bg)] text-[var(--heading)] shadow-sm"
                : "text-[var(--text-secondary)]"
            }`}
          >
            Admin
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {mode === "admin" && (
            <div>
              <label className="block text-[13px] font-medium text-[var(--text-secondary)] mb-1">
                Password
              </label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Admin password"
                required
                className="w-full px-3 py-2 text-[14px] bg-[var(--bg)] border border-[var(--border)] rounded text-[var(--text)] placeholder-[var(--muted)] focus:outline-none focus:border-[var(--accent-light)]"
              />
            </div>
          )}

          {mode === "user" && (
            <div>
              <label className="block text-[13px] font-medium text-[var(--text-secondary)] mb-1">
                API Key <span className="text-[var(--muted)]">(optional — for AI features)</span>
              </label>
              <input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="sk-ant-... (leave empty for read-only access)"
                className="w-full px-3 py-2 text-[14px] bg-[var(--bg)] border border-[var(--border)] rounded text-[var(--text)] placeholder-[var(--muted)] focus:outline-none focus:border-[var(--accent-light)]"
              />
              <p className="text-[11px] text-[var(--muted)] mt-1">
                Without a key you can browse notes, search, and view the graph.
                AI-powered search and Q&A require an Anthropic API key.
              </p>
            </div>
          )}

          {error && (
            <p className="text-[13px] text-[var(--danger)]">{error}</p>
          )}

          <button
            type="submit"
            disabled={loading || (mode === "admin" && !password)}
            className="w-full py-2.5 bg-[var(--accent)] hover:bg-[var(--link)] disabled:opacity-50 text-white text-[14px] font-medium rounded-lg transition-colors"
          >
            {loading ? "Signing in..." : mode === "admin" ? "Sign in as Admin" : "Continue"}
          </button>
        </form>

        {mode === "admin" && (
          <button
            onClick={handleGuest}
            className="w-full mt-3 py-2 text-[13px] text-[var(--text-secondary)] hover:text-[var(--text)] transition-colors"
          >
            Continue as reader instead
          </button>
        )}
      </div>
    </div>
  );
}
