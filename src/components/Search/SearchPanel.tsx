"use client";

import { useState, useRef, useEffect } from "react";
import Link from "next/link";
import AnswerView from "./AnswerView";

interface SmartResult {
  id: number;
  title: string;
  slug: string;
  summary: string | null;
  tags: string[];
  relevance: string;
  score: number;
  source: string | null;
  chapter: string | null;
  page: number | null;
  level: number;
}

interface SmartSearchResponse {
  query: string;
  interpretation: string;
  results: SmartResult[];
  suggested_queries: string[];
}

interface KeywordResult {
  id: number;
  title: string;
  slug: string;
  excerpt: string;
  tags: string[];
  matchType: string;
  score: number;
}

type Mode = "smart" | "keyword" | "ask";

export default function SearchPanel({
  initialQuery = "",
}: {
  initialQuery?: string;
}) {
  const [query, setQuery] = useState(initialQuery);
  const [mode, setMode] = useState<Mode>("smart");
  const [smartResults, setSmartResults] = useState<SmartSearchResponse | null>(
    null
  );
  const [keywordResults, setKeywordResults] = useState<KeywordResult[]>([]);
  const [answer, setAnswer] = useState("");
  const [statusMessages, setStatusMessages] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [answering, setAnswering] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;

    if (mode === "ask") {
      await handleAsk();
    } else if (mode === "smart") {
      await handleSmartSearch();
    } else {
      await handleKeywordSearch();
    }
  }

  async function handleSmartSearch(q?: string) {
    const searchQuery = q || query;
    if (q) setQuery(q);
    setLoading(true);
    setSmartResults(null);
    setKeywordResults([]);
    setAnswer("");
    try {
      // Try AI-powered smart search first
      let res = await fetch(
        `/api/search/smart?q=${encodeURIComponent(searchQuery)}`
      );

      // If forbidden (no API key), fall back to graph-powered enhanced search
      if (res.status === 403) {
        res = await fetch(
          `/api/search/enhanced?q=${encodeURIComponent(searchQuery)}`
        );
      }

      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const text = await res.text();
      try {
        const data: SmartSearchResponse = JSON.parse(text);
        setSmartResults(data);
      } catch {
        throw new Error("Invalid response from server");
      }
    } catch {
      setMode("keyword");
      await handleKeywordSearch();
    } finally {
      setLoading(false);
    }
  }

  async function handleKeywordSearch() {
    setLoading(true);
    setSmartResults(null);
    setKeywordResults([]);
    setAnswer("");
    try {
      const res = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const text = await res.text();
      try {
        setKeywordResults(JSON.parse(text));
      } catch {
        setKeywordResults([]);
      }
    } finally {
      setLoading(false);
    }
  }

  async function handleAsk() {
    setAnswering(true);
    setAnswer("");
    setStatusMessages([]);
    setSmartResults(null);
    setKeywordResults([]);

    const STATUS_PREFIX = "<<STATUS>>";

    try {
      const res = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: query }),
      });
      const reader = res.body?.getReader();
      const decoder = new TextDecoder();

      if (reader) {
        let leftover = "";
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          const text = leftover + decoder.decode(value, { stream: true });
          leftover = "";

          // Split by newlines to process line by line
          const lines = text.split("\n");

          // Last element may be incomplete — save for next chunk
          leftover = lines.pop() || "";

          for (const line of lines) {
            if (line.startsWith(STATUS_PREFIX)) {
              const msg = line.slice(STATUS_PREFIX.length).trim();
              if (msg) setStatusMessages((prev) => [...prev, msg]);
            } else {
              // Answer content — append with the newline we split on
              setAnswer((prev) => prev + line + "\n");
            }
          }
        }

        // Flush leftover
        if (leftover) {
          if (leftover.startsWith(STATUS_PREFIX)) {
            const msg = leftover.slice(STATUS_PREFIX.length).trim();
            if (msg) setStatusMessages((prev) => [...prev, msg]);
          } else {
            setAnswer((prev) => prev + leftover);
          }
        }
      }
    } catch {
      setAnswer("Failed to get an answer. Please try again.");
    } finally {
      setAnswering(false);
    }
  }

  const scoreColor = (score: number) => {
    if (score >= 0.8) return "text-[var(--callout-green)]";
    if (score >= 0.5) return "text-[var(--callout-amber)]";
    return "text-[var(--muted)]";
  };

  const scoreBarColor = (score: number) => {
    if (score >= 0.8) return "var(--callout-green)";
    if (score >= 0.5) return "var(--callout-amber)";
    return "var(--muted)";
  };

  const levelLabel = (level: number) => {
    if (level === 0) return "Index";
    if (level === 1) return "Topic";
    if (level === 2) return "Subtopic";
    return "Detail";
  };

  return (
    <div className="max-w-3xl mx-auto px-8 py-8">
      <div className="mb-8">
        <h1 className="text-[24px] font-bold text-[var(--heading)] mb-1">Search</h1>
        <p className="text-[var(--text-secondary)] text-sm">Find notes by meaning, keywords, or ask a question</p>
      </div>

      <form onSubmit={handleSubmit} className="mb-6">
        <div className="relative">
          <input ref={inputRef} type="text" value={query} onChange={(e) => setQuery(e.target.value)}
            placeholder={mode === "ask" ? "Ask a question..." : mode === "smart" ? "Describe what you're looking for..." : "Search by keyword..."}
            className="w-full pl-4 pr-24 py-3 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text)] placeholder-[var(--muted)] focus:outline-none focus:ring-2 focus:ring-[var(--accent)]/30 focus:border-[var(--accent)] transition-all"
          />
          <button type="submit" disabled={loading || answering}
            className="absolute right-2 top-1/2 -translate-y-1/2 px-4 py-1.5 rounded-md bg-[var(--accent)] text-white text-sm font-medium hover:bg-[var(--link)] disabled:opacity-50 transition-colors">
            {loading || answering ? (
              <span className="inline-flex items-center gap-1.5">
                <span className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                {mode === "ask" ? "Thinking" : "Searching"}
              </span>
            ) : mode === "ask" ? "Ask" : "Search"}
          </button>
        </div>

        <div className="flex gap-1 mt-3 p-1 bg-[var(--surface)] rounded-lg w-fit border border-[var(--border-light)]">
          {([["smart", "Smart Search"], ["keyword", "Keyword"], ["ask", "Ask AI"]] as const).map(([m, label]) => (
            <button key={m} type="button" onClick={() => setMode(m)}
              className={`px-3 py-1.5 text-xs font-medium rounded-md transition-all ${
                mode === m ? "bg-[var(--bg)] text-[var(--heading)] shadow-sm" : "text-[var(--text-secondary)] hover:text-[var(--text)]"
              }`}>
              {label}
            </button>
          ))}
        </div>
      </form>

      {smartResults && (
        <div className="space-y-4">
          <div className="callout callout-summary">
            <div className="callout-title">&#x1F50D; {smartResults.interpretation}</div>
            <p className="text-[12px] text-[var(--text-secondary)] mt-0.5">
              {smartResults.results.length} relevant note{smartResults.results.length !== 1 ? "s" : ""} found
            </p>
          </div>

          <div className="space-y-2">
            {smartResults.results.map((r, idx) => (
              <Link key={r.id} href={`/notes/${r.slug}`}
                className="group block rounded-lg border border-[var(--border)] bg-[var(--bg)] hover:border-[var(--accent-light)] hover:shadow-sm transition-all">
                <div className="p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-[var(--muted)] font-mono">{idx + 1}</span>
                        <h3 className="font-semibold text-[var(--heading)] group-hover:text-[var(--link)] truncate transition-colors">{r.title}</h3>
                      </div>
                      <p className="text-sm text-[var(--text-secondary)] mt-1.5 leading-relaxed">{r.relevance}</p>
                    </div>
                    <div className="flex-shrink-0 flex flex-col items-center gap-1">
                      <span className={`text-sm font-semibold tabular-nums ${scoreColor(r.score)}`}>{Math.round(r.score * 100)}%</span>
                      <div className="w-8 h-1.5 rounded-full bg-[var(--surface2)] overflow-hidden">
                        <div className="h-full rounded-full" style={{ width: `${r.score * 100}%`, backgroundColor: scoreBarColor(r.score) }} />
                      </div>
                    </div>
                  </div>
                  {r.summary && <p className="text-xs text-[var(--muted)] mt-2 line-clamp-2">{r.summary}</p>}
                  <div className="flex items-center flex-wrap gap-2 mt-3">
                    <span className="px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider rounded bg-[var(--surface2)] text-[var(--text-secondary)]">{levelLabel(r.level)}</span>
                    {r.source && <span className="text-[10px] text-[var(--muted)]">{r.source}{r.chapter && ` · ${r.chapter}`}{r.page && ` · p.${r.page}`}</span>}
                    {r.tags.slice(0, 4).map((tag) => <span key={tag} className="tag-pill text-[10px]">{tag}</span>)}
                    {r.tags.length > 4 && <span className="text-[10px] text-[var(--muted)]">+{r.tags.length - 4}</span>}
                  </div>
                </div>
              </Link>
            ))}
          </div>

          {smartResults.suggested_queries.length > 0 && (
            <div className="pt-2">
              <p className="text-xs font-medium text-[var(--muted)] mb-2">Related searches</p>
              <div className="flex flex-wrap gap-2">
                {smartResults.suggested_queries.map((sq) => (
                  <button key={sq} onClick={() => { setMode("smart"); handleSmartSearch(sq); }}
                    className="px-3 py-1.5 text-xs rounded-full border border-[var(--border)] text-[var(--text-secondary)] hover:bg-[var(--accent-bg)] hover:border-[var(--accent-light)] hover:text-[var(--accent)] transition-all">
                    {sq}
                  </button>
                ))}
              </div>
            </div>
          )}

          {smartResults.results.length === 0 && (
            <div className="text-center py-8 text-[var(--muted)] text-sm">No relevant notes found.</div>
          )}
        </div>
      )}

      {mode === "keyword" && keywordResults.length > 0 && (
        <div className="space-y-2">
          {keywordResults.map((r) => (
            <Link key={r.id} href={`/notes/${r.slug}`}
              className="group block p-4 rounded-lg border border-[var(--border)] bg-[var(--bg)] hover:border-[var(--accent-light)] hover:shadow-sm transition-all">
              <h3 className="font-medium text-[var(--heading)] group-hover:text-[var(--link)] transition-colors">{r.title}</h3>
              <p className="text-sm text-[var(--text-secondary)] mt-1 line-clamp-2">{r.excerpt}</p>
              {r.tags.length > 0 && (
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {r.tags.slice(0, 5).map((tag) => <span key={tag} className="tag-pill text-[10px]">{tag}</span>)}
                </div>
              )}
            </Link>
          ))}
        </div>
      )}

      {mode === "keyword" && !loading && keywordResults.length === 0 && query && (
        <div className="text-center py-8 text-[var(--muted)] text-sm">No keyword matches found.</div>
      )}

      {mode === "ask" && (answer || answering || statusMessages.length > 0) && (
        <AnswerView
          question={query}
          answer={answer}
          isStreaming={answering}
          statusMessages={statusMessages}
        />
      )}

      {loading && (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="p-4 rounded-lg border border-[var(--border)] animate-pulse">
              <div className="h-5 w-2/3 bg-[var(--surface2)] rounded mb-3" />
              <div className="h-3 w-full bg-[var(--surface)] rounded mb-2" />
              <div className="h-3 w-4/5 bg-[var(--surface)] rounded" />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
