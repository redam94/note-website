"use client";

import { useMemo, useState, useEffect, useRef } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";

// ── Callout metadata ─────────────────────────────────────────────────

const CALLOUT_META: Record<string, { icon: string; label: string; cls: string }> = {
  summary:    { icon: "📋", label: "Summary",    cls: "callout-summary" },
  abstract:   { icon: "📋", label: "Abstract",   cls: "callout-abstract" },
  definition: { icon: "📖", label: "Definition", cls: "callout-definition" },
  theorem:    { icon: "📐", label: "Theorem",    cls: "callout-theorem" },
  example:    { icon: "💡", label: "Example",    cls: "callout-example" },
  important:  { icon: "❗", label: "Important",  cls: "callout-important" },
  warning:    { icon: "⚠️", label: "Warning",    cls: "callout-warning" },
  tip:        { icon: "💡", label: "Tip",        cls: "callout-tip" },
};

// ── Block types ──────────────────────────────────────────────────────

type Block =
  | { type: "markdown"; content: string }
  | { type: "callout"; cls: string; icon: string; label: string; body: string };

function parseAnswer(raw: string): Block[] {
  const withLinks = raw.replace(
    /\[\[([^\]|]+?)(?:\|([^\]]+?))?\]\]/g,
    (_m, target, display) => {
      const text = display || target;
      const slug = target.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
      return `[${text}](/notes/${slug})`;
    }
  );

  const fixedMath = withLinks.replace(/^\$\$(.+)\$\$$/gm, (_m, inner) => `$$\n${inner}\n$$`);

  const lines = fixedMath.split("\n");
  const blocks: Block[] = [];
  let buf: string[] = [];
  let i = 0;

  function flush() {
    const t = buf.join("\n").trim();
    if (t) blocks.push({ type: "markdown", content: t });
    buf = [];
  }

  while (i < lines.length) {
    const m =
      lines[i].match(/^>\s*\[!(\w+)\]\s*(.*)/) ||
      lines[i].match(/^\[!(\w+)\]\s*(.*)/);
    if (m) {
      flush();
      const type = m[1].toLowerCase();
      const title = m[2] || "";
      const meta = CALLOUT_META[type] || CALLOUT_META.tip;
      const label = title || meta.label;

      const hasQuotePrefix = lines[i].trimStart().startsWith(">");
      const bodyLines: string[] = [];
      i++;
      while (i < lines.length) {
        const line = lines[i];
        if (line.match(/^>\s/) || line === ">") {
          bodyLines.push(line.replace(/^>\s?/, ""));
          i++;
        } else if (!hasQuotePrefix && line.trim() && !line.match(/^(#{1,4}\s|>\s*\[!|\[!)/) && !line.match(/^\s*$/)) {
          bodyLines.push(line);
          i++;
        } else {
          break;
        }
      }
      if (!hasQuotePrefix) {
        while (i < lines.length && lines[i].trim() === "") { i++; }
      }
      const body = bodyLines
        .filter((l) => !l.match(/^\^[\w-]+$/))
        .join("\n")
        .replace(/^\$\$(.+)\$\$$/gm, (_m2, inner) => `$$\n${inner}\n$$`);

      blocks.push({ type: "callout", cls: meta.cls, icon: meta.icon, label, body });
    } else {
      if (!lines[i].match(/^\^[\w-]+$/)) buf.push(lines[i]);
      i++;
    }
  }
  flush();
  return blocks;
}

// ── Markdown components ──────────────────────────────────────────────

const mdComponents = {
  a: ({ href, children }: any) => {
    if (href?.startsWith("/notes/")) {
      return <Link href={href} className="text-[var(--link)] hover:underline">{children}</Link>;
    }
    return <a href={href} className="text-[var(--link)] hover:underline">{children}</a>;
  },
  table: ({ children, ...props }: any) => (
    <div className="table-wrapper">
      <table {...props}>{children}</table>
    </div>
  ),
};

// ── Processing steps config ──────────────────────────────────────────

const STEP_ICONS: Record<string, string> = {
  searching: "🔍",
  found: "📚",
  building: "🔧",
  sources: "📎",
  generating: "✍️",
};

function getStepIcon(msg: string): string {
  const lower = msg.toLowerCase();
  if (lower.includes("searching")) return STEP_ICONS.searching;
  if (lower.includes("found")) return STEP_ICONS.found;
  if (lower.includes("building")) return STEP_ICONS.building;
  if (lower.includes("sources")) return STEP_ICONS.sources;
  if (lower.includes("generating")) return STEP_ICONS.generating;
  return "⏳";
}

// ── AnswerView component ─────────────────────────────────────────────

interface AnswerViewProps {
  question: string;
  answer: string;
  isStreaming: boolean;
  statusMessages: string[];
}

export default function AnswerView({ question, answer, isStreaming, statusMessages }: AnswerViewProps) {
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<{ slug: string } | null>(null);
  const router = useRouter();
  const bottomRef = useRef<HTMLDivElement>(null);

  // Parse blocks from whatever answer text we have so far
  const blocks = useMemo(() => (answer ? parseAnswer(answer) : []), [answer]);

  // Auto-scroll to bottom as content streams in
  useEffect(() => {
    if (isStreaming && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [answer, isStreaming]);

  async function handleSave() {
    setSaving(true);
    try {
      const res = await fetch("/api/ask/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, answer }),
      });
      if (!res.ok) throw new Error();
      const data = await res.json();
      setSaved(data);
      window.dispatchEvent(new Event("sidebar-refresh"));
    } catch {
      setSaving(false);
    }
  }

  const hasAnswer = answer.trim().length > 0;
  const showProcessing = isStreaming && !hasAnswer;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="tag-pill">#type/qa</span>
          {isStreaming && hasAnswer && (
            <span className="inline-flex items-center gap-1.5 text-[12px] text-[var(--accent)]">
              <span className="w-2.5 h-2.5 border-2 border-[var(--accent)]/30 border-t-[var(--accent)] rounded-full animate-spin" />
              Streaming...
            </span>
          )}
        </div>
        {!isStreaming && hasAnswer && !saved && (
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex items-center gap-1.5 px-3 py-1.5 text-[12px] font-medium rounded-md border border-[var(--border)] text-[var(--text-secondary)] hover:bg-[var(--accent-bg)] hover:border-[var(--accent-light)] hover:text-[var(--accent)] disabled:opacity-50 transition-all"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
            </svg>
            {saving ? "Saving..." : "Save to knowledge base"}
          </button>
        )}
        {saved && (
          <Link
            href={`/notes/${saved.slug}`}
            className="flex items-center gap-1.5 px-3 py-1.5 text-[12px] font-medium rounded-md bg-[var(--accent-bg)] text-[var(--accent)]"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
            </svg>
            Saved — view note
          </Link>
        )}
      </div>

      {/* Question title */}
      <h2 className="text-[20px] font-bold text-[var(--heading)]">{question}</h2>

      {/* Processing status (shown before answer starts streaming) */}
      {statusMessages.length > 0 && (
        <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4">
          <div className="space-y-2">
            {statusMessages.map((msg, i) => {
              const isLatest = i === statusMessages.length - 1;
              const isDone = !isLatest || hasAnswer;
              return (
                <div key={i} className="flex items-center gap-2.5">
                  {isDone ? (
                    <span className="w-4 h-4 rounded-full bg-[var(--accent)] flex items-center justify-center flex-shrink-0">
                      <svg className="w-2.5 h-2.5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                      </svg>
                    </span>
                  ) : (
                    <span className="w-4 h-4 border-2 border-[var(--accent)] rounded-full flex items-center justify-center flex-shrink-0">
                      <span className="w-1.5 h-1.5 bg-[var(--accent)] rounded-full animate-pulse" />
                    </span>
                  )}
                  <span className={`text-[13px] ${isDone ? "text-[var(--text-secondary)]" : "text-[var(--text)]"}`}>
                    {getStepIcon(msg)} {msg}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Rendered answer with callouts + math */}
      {hasAnswer && (
        <div className="prose max-w-none text-[15px] leading-[1.8]">
          {blocks.map((block, idx) => {
            if (block.type === "callout") {
              return (
                <div key={idx} className={`callout ${block.cls}`}>
                  <div className="callout-title">{block.icon} {block.label}</div>
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm, remarkMath]}
                    rehypePlugins={[rehypeKatex]}
                    components={mdComponents}
                  >
                    {block.body}
                  </ReactMarkdown>
                </div>
              );
            }
            return (
              <ReactMarkdown
                key={idx}
                remarkPlugins={[remarkGfm, remarkMath]}
                rehypePlugins={[rehypeKatex]}
                components={mdComponents}
              >
                {block.content}
              </ReactMarkdown>
            );
          })}
          {/* Streaming cursor */}
          {isStreaming && (
            <span className="inline-block w-2 h-4 bg-[var(--accent)] animate-pulse ml-0.5 align-text-bottom rounded-sm" />
          )}
        </div>
      )}

      {/* Loading skeleton when no answer and no status yet */}
      {showProcessing && statusMessages.length === 0 && (
        <div className="flex items-center gap-2 text-[var(--text-secondary)] text-[13px]">
          <span className="w-4 h-4 border-2 border-[var(--accent)]/30 border-t-[var(--accent)] rounded-full animate-spin" />
          Preparing...
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  );
}
