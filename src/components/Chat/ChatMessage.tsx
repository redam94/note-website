"use client";

import { useMemo } from "react";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";

// ── Types ────────────────────────────────────────────────────────────

export interface SourceNote {
  id: number;
  title: string;
  slug: string;
  chapter: string | null;
  page: number | null;
  summary: string | null;
}

export interface Message {
  role: "user" | "assistant";
  content: string;
  sources?: SourceNote[];
  statusMessages?: string[];
  isStreaming?: boolean;
  timestamp?: number;
}

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

type Block =
  | { type: "markdown"; content: string }
  | { type: "callout"; cls: string; icon: string; label: string; body: string };

function parseBlocks(raw: string): Block[] {
  // Convert wiki-links to markdown links
  const withLinks = raw.replace(
    /\[\[([^\]|]+?)(?:\|([^\]]+?))?\]\]/g,
    (_m, target, display) => {
      const text = display || target;
      const slug = target.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
      return `[${text}](/notes/${slug})`;
    }
  );
  // Fix single-line display math
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
    const m = lines[i].match(/^>\s*\[!(\w+)\]\s*(.*)/) || lines[i].match(/^\[!(\w+)\]\s*(.*)/);
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
  a: ({ href, children }: { href?: string; children?: React.ReactNode }) => {
    if (href?.startsWith("/notes/")) {
      return <Link href={href} className="text-[var(--link)] hover:underline">{children}</Link>;
    }
    return <a href={href} target="_blank" rel="noopener noreferrer" className="text-[var(--link)] hover:underline">{children}</a>;
  },
  table: ({ children, ...props }: React.HTMLAttributes<HTMLTableElement>) => (
    <div className="table-wrapper overflow-x-auto my-3">
      <table {...props}>{children}</table>
    </div>
  ),
};

// ── Source card strip ────────────────────────────────────────────────

function SourceStrip({ sources }: { sources: SourceNote[] }) {
  if (!sources.length) return null;
  return (
    <div className="flex gap-2 overflow-x-auto pb-1 mt-3 scrollbar-thin">
      {sources.map((s) => (
        <Link
          key={s.id}
          href={`/notes/${s.slug}`}
          className="flex-shrink-0 flex items-start gap-2 px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--surface)] hover:border-[var(--accent-light)] hover:bg-[var(--accent-bg)] transition-all max-w-[220px] group"
        >
          <svg className="w-3.5 h-3.5 mt-0.5 text-[var(--muted)] group-hover:text-[var(--accent)] flex-shrink-0 transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
          <div className="min-w-0">
            <p className="text-[12px] font-medium text-[var(--heading)] group-hover:text-[var(--accent)] truncate transition-colors leading-tight">
              {s.title}
            </p>
            {(s.chapter || s.page) && (
              <p className="text-[10px] text-[var(--muted)] mt-0.5 truncate">
                {[s.chapter, s.page != null ? `p. ${s.page}` : null].filter(Boolean).join(" · ")}
              </p>
            )}
          </div>
        </Link>
      ))}
    </div>
  );
}

// ── Status steps ─────────────────────────────────────────────────────

function StatusSteps({ messages, isDone }: { messages: string[]; isDone: boolean }) {
  if (!messages.length) return null;
  return (
    <div className="space-y-1.5 mb-3">
      {messages.map((msg, i) => {
        const isLatest = i === messages.length - 1;
        const done = isDone || !isLatest;
        return (
          <div key={i} className="flex items-center gap-2">
            {done ? (
              <span className="w-3.5 h-3.5 rounded-full bg-[var(--accent)] flex items-center justify-center flex-shrink-0">
                <svg className="w-2 h-2 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                </svg>
              </span>
            ) : (
              <span className="w-3.5 h-3.5 border-2 border-[var(--accent)] rounded-full flex items-center justify-center flex-shrink-0">
                <span className="w-1 h-1 bg-[var(--accent)] rounded-full animate-pulse" />
              </span>
            )}
            <span className={`text-[12px] ${done ? "text-[var(--muted)]" : "text-[var(--text-secondary)]"}`}>
              {msg}
            </span>
          </div>
        );
      })}
    </div>
  );
}

// ── ChatMessage component ────────────────────────────────────────────

export default function ChatMessage({ message }: { message: Message }) {
  const blocks = useMemo(
    () => (message.content ? parseBlocks(message.content) : []),
    [message.content]
  );

  if (message.role === "user") {
    return (
      <div className="flex justify-end mb-4">
        <div className="max-w-[75%] px-4 py-2.5 rounded-2xl rounded-tr-sm bg-[var(--accent)] text-white text-[14px] leading-relaxed whitespace-pre-wrap">
          {message.content}
        </div>
      </div>
    );
  }

  // Assistant message
  const hasContent = message.content.trim().length > 0;
  const isDone = !message.isStreaming;

  return (
    <div className="mb-6">
      {/* Status steps */}
      {(message.statusMessages?.length ?? 0) > 0 && (
        <StatusSteps messages={message.statusMessages!} isDone={isDone && hasContent} />
      )}

      {/* Thinking spinner before content arrives */}
      {!hasContent && message.isStreaming && (
        <div className="flex items-center gap-2 text-[var(--muted)] text-[13px] py-2">
          <span className="w-4 h-4 border-2 border-[var(--accent)]/30 border-t-[var(--accent)] rounded-full animate-spin" />
          Thinking...
        </div>
      )}

      {/* Answer content */}
      {hasContent && (
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
          {message.isStreaming && (
            <span className="inline-block w-1.5 h-4 bg-[var(--accent)] animate-pulse ml-0.5 align-text-bottom rounded-sm" />
          )}
        </div>
      )}

      {/* Source cards */}
      {(message.sources?.length ?? 0) > 0 && (
        <SourceStrip sources={message.sources!} />
      )}
    </div>
  );
}
