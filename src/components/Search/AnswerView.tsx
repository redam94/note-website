"use client";

import { Fragment, useMemo, useState } from "react";
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
  // Wiki-links → markdown links
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
    const m = lines[i].match(/^>\s*\[!(\w+)\]\s*(.*)/);
    if (m) {
      flush();
      const type = m[1].toLowerCase();
      const title = m[2] || "";
      const meta = CALLOUT_META[type] || CALLOUT_META.tip;
      const label = title || meta.label;

      const bodyLines: string[] = [];
      i++;
      while (i < lines.length && lines[i].match(/^>/)) {
        bodyLines.push(lines[i].replace(/^>\s?/, ""));
        i++;
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
};

// ── AnswerView component ─────────────────────────────────────────────

interface AnswerViewProps {
  question: string;
  answer: string;
  isStreaming: boolean;
}

export default function AnswerView({ question, answer, isStreaming }: AnswerViewProps) {
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<{ slug: string } | null>(null);
  const router = useRouter();

  const blocks = useMemo(() => parseAnswer(answer), [answer]);

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
    } catch {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="tag-pill">#type/qa</span>
          {isStreaming && (
            <span className="inline-flex items-center gap-1.5 text-[12px] text-[var(--accent)]">
              <span className="w-2.5 h-2.5 border-2 border-[var(--accent)]/30 border-t-[var(--accent)] rounded-full animate-spin" />
              Generating...
            </span>
          )}
        </div>
        {!isStreaming && answer && !saved && (
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
      <h2 className="text-[20px] font-bold text-[var(--heading)]">
        {question}
      </h2>

      {/* Rendered answer with callouts + math */}
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
      </div>
    </div>
  );
}
