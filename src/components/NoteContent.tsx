"use client";

import { useMemo } from "react";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";

// ── Callout metadata ────────────────────────────────────────────────

const CALLOUT_META: Record<string, { icon: string; label: string; cls: string }> = {
  summary:    { icon: "📋", label: "Summary",    cls: "callout-summary" },
  abstract:   { icon: "📋", label: "Abstract",   cls: "callout-abstract" },
  definition: { icon: "📖", label: "Definition", cls: "callout-definition" },
  theorem:    { icon: "📐", label: "Theorem",    cls: "callout-theorem" },
  example:    { icon: "💡", label: "Example",    cls: "callout-example" },
  important:  { icon: "❗", label: "Important",  cls: "callout-important" },
  warning:    { icon: "⚠️", label: "Warning",    cls: "callout-warning" },
  tip:        { icon: "💡", label: "Tip",        cls: "callout-tip" },
  info:       { icon: "ℹ️", label: "Info",       cls: "callout-summary" },
  note:       { icon: "📝", label: "Note",       cls: "callout-summary" },
};

type ContentBlock =
  | { type: "markdown"; content: string }
  | { type: "callout"; cls: string; icon: string; label: string; body: string };

// ── Content parser ──────────────────────────────────────────────────

function parseContent(raw: string): ContentBlock[] {
  const stripped = raw.replace(/^---[\s\S]*?---\n*/m, "");

  const withLinks = stripped.replace(
    /\[\[([^\]|]+?)(?:\|([^\]]+?))?\]\]/g,
    (_match, target, display) => {
      const text = display || target;
      const slug = target.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
      return `[${text}](/notes/${slug})`;
    }
  );

  const fixedMath = withLinks.replace(
    /^\$\$(.+)\$\$$/gm,
    (_match, inner) => `$$\n${inner}\n$$`
  );

  const lines = fixedMath.split("\n");
  const blocks: ContentBlock[] = [];
  let mdBuffer: string[] = [];
  let i = 0;

  function flushMd() {
    const text = mdBuffer.join("\n").trim();
    if (text) blocks.push({ type: "markdown", content: text });
    mdBuffer = [];
  }

  while (i < lines.length) {
    const calloutMatch =
      lines[i].match(/^>\s*\[!(\w+)\]\s*(.*)/) ||
      lines[i].match(/^\[!(\w+)\]\s*(.*)/);

    if (calloutMatch) {
      flushMd();
      const type = calloutMatch[1].toLowerCase();
      const title = calloutMatch[2] || "";
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
        .replace(/^\$\$(.+)\$\$$/gm, (_m, inner) => `$$\n${inner}\n$$`);

      blocks.push({ type: "callout", cls: meta.cls, icon: meta.icon, label, body });
    } else {
      if (!lines[i].match(/^\^[\w-]+$/)) {
        mdBuffer.push(lines[i]);
      }
      i++;
    }
  }
  flushMd();
  return blocks;
}

// ── Helpers ─────────────────────────────────────────────────────────

function extractText(children: React.ReactNode): string {
  if (typeof children === "string") return children;
  if (Array.isArray(children)) return children.map(extractText).join("");
  if (children && typeof children === "object" && "props" in children) {
    return extractText((children as any).props.children);
  }
  return "";
}

function slugify(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

const mdComponents = {
  h1: ({ children, ...props }: any) => <h1 id={slugify(extractText(children))} {...props}>{children}</h1>,
  h2: ({ children, ...props }: any) => <h2 id={slugify(extractText(children))} {...props}>{children}</h2>,
  h3: ({ children, ...props }: any) => <h3 id={slugify(extractText(children))} {...props}>{children}</h3>,
  h4: ({ children, ...props }: any) => <h4 id={slugify(extractText(children))} {...props}>{children}</h4>,
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

// ── Component ───────────────────────────────────────────────────────

interface NoteContentProps {
  content: string;
  className?: string;
}

export default function NoteContent({ content, className }: NoteContentProps) {
  const blocks = useMemo(() => parseContent(content), [content]);

  return (
    <div className={`prose max-w-none text-[15px] leading-[1.8] ${className || ""}`}>
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
  );
}
