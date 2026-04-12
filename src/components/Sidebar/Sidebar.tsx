"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { GraphNode } from "@/types";

interface TreeNode {
  id: number;
  title: string;
  slug: string;
  level: number;
  children: TreeNode[];
}

export default function Sidebar() {
  const [searchQuery, setSearchQuery] = useState("");
  const [tree, setTree] = useState<TreeNode[]>([]);
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());
  const pathname = usePathname();

  useEffect(() => {
    fetchTree();
  }, []);

  async function fetchTree() {
    try {
      const res = await fetch("/api/graph");
      if (!res.ok) return;
      const data = await res.json();
      const nodes = data.nodes as GraphNode[];
      const nodeMap = new Map<number, TreeNode>();
      for (const n of nodes) nodeMap.set(n.id, { ...n, children: [] });

      const roots: TreeNode[] = [];
      for (const n of nodes) {
        if (n.level <= 1) {
          const tn = nodeMap.get(n.id);
          if (tn) roots.push(tn);
        }
      }
      for (const edge of data.edges) {
        if (edge.relationship === "part_of") {
          const parent = nodeMap.get(edge.source);
          const child = nodeMap.get(edge.target);
          if (parent && child) parent.children.push(child);
        }
      }
      setTree(roots);
    } catch { /* silent */ }
  }

  function toggleExpand(id: number) {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  function shortenTitle(title: string): string {
    // Strip common verbose prefixes
    let short = title
      .replace(/^(Index|Overview|Introduction|Summary):\s*/i, "")
      .replace(/\s+in\s+(SMM|ABM|Copula|Market|Consumer)\s+.*/i, (m) =>
        m.length > 25 ? "" : m
      );
    // Truncate at colon if the result is still long
    if (short.length > 35 && short.includes(": ")) {
      short = short.split(": ")[0];
    }
    // Final truncation
    if (short.length > 40) {
      short = short.slice(0, 37) + "...";
    }
    return short || title.slice(0, 37) + "...";
  }

  function renderTreeNode(node: TreeNode, depth: number = 0) {
    const hasChildren = node.children.length > 0;
    const isExpanded = expandedIds.has(node.id);
    const isActive = pathname === `/notes/${node.slug}`;

    return (
      <li key={node.id}>
        <div
          className={`flex items-center gap-1 py-[3px] rounded transition-colors ${
            isActive
              ? "bg-[var(--accent-bg-hover)] text-[var(--accent)]"
              : "text-[var(--text-secondary)] hover:text-[var(--text)] hover:bg-[var(--surface2)]"
          }`}
          style={{ paddingLeft: `${depth * 16 + 8}px` }}
        >
          {hasChildren ? (
            <button
              onClick={() => toggleExpand(node.id)}
              className="w-4 h-4 flex items-center justify-center flex-shrink-0 text-[10px] text-[var(--muted)]"
            >
              {isExpanded ? "▾" : "▸"}
            </button>
          ) : (
            <span className="w-4 h-4 flex items-center justify-center flex-shrink-0 text-[8px] text-[var(--border)]">
              ◆
            </span>
          )}
          <Link href={`/notes/${node.slug}`} className="text-[13px] truncate flex-1" title={node.title}>
            {shortenTitle(node.title)}
          </Link>
        </div>
        {hasChildren && isExpanded && (
          <ul>{node.children.map((child) => renderTreeNode(child, depth + 1))}</ul>
        )}
      </li>
    );
  }

  return (
    <aside className="w-60 h-full flex flex-col bg-[var(--surface)] border-r border-[var(--border)] overflow-hidden flex-shrink-0">
      {/* Title */}
      <div className="px-4 pt-5 pb-3">
        <Link href="/" className="text-lg font-semibold text-[var(--heading)] hover:text-[var(--accent)] transition-colors">
          Second Brain
        </Link>
      </div>

      {/* Search */}
      <div className="px-3 pb-3">
        <div className="relative">
          <svg className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-[var(--muted)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input
            type="text"
            placeholder="Search..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && searchQuery.trim()) {
                window.location.href = `/search?q=${encodeURIComponent(searchQuery)}`;
              }
            }}
            className="w-full pl-8 pr-3 py-1.5 text-[13px] bg-[var(--bg)] border border-[var(--border)] rounded text-[var(--text)] placeholder-[var(--muted)] focus:outline-none focus:border-[var(--accent-light)]"
          />
        </div>
      </div>

      {/* Explorer */}
      <div className="flex-1 overflow-y-auto px-1">
        <div className="px-2 py-1.5 flex items-center justify-between">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-[var(--muted)]">
            Explorer
          </span>
          <div className="flex gap-1">
            <Link href="/maintenance" className="p-1 text-[var(--muted)] hover:text-[var(--accent)] transition-colors" title="Maintenance">
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
            </Link>
            <Link href="/upload" className="p-1 text-[var(--muted)] hover:text-[var(--accent)] transition-colors" title="Upload">
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
              </svg>
            </Link>
            <Link href="/settings" className="p-1 text-[var(--muted)] hover:text-[var(--accent)] transition-colors" title="Settings">
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
            </Link>
          </div>
        </div>

        {tree.length === 0 ? (
          <p className="text-[12px] text-[var(--muted)] px-3 py-4">No notes yet.</p>
        ) : (
          <ul>{tree.map((n) => renderTreeNode(n))}</ul>
        )}
      </div>
    </aside>
  );
}
