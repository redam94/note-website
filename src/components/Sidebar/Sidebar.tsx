"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { GraphNode, GraphEdgeData } from "@/types";

interface TreeNode {
  id: number;
  title: string;
  slug: string;
  level: number;
  children: TreeNode[];
}

// Global refresh trigger — other components can call window.dispatchEvent(new Event("sidebar-refresh"))
const REFRESH_EVENT = "sidebar-refresh";

export default function Sidebar() {
  const [searchQuery, setSearchQuery] = useState("");
  const [tree, setTree] = useState<TreeNode[]>([]);
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());
  const pathname = usePathname();
  const prevPathRef = useRef(pathname);

  // Refetch when pathname changes (navigation after add/delete)
  useEffect(() => {
    if (prevPathRef.current !== pathname) {
      prevPathRef.current = pathname;
      fetchTree();
    }
  }, [pathname]);

  // Initial fetch + listen for explicit refresh events
  useEffect(() => {
    fetchTree();
    const handler = () => fetchTree();
    window.addEventListener(REFRESH_EVENT, handler);
    return () => window.removeEventListener(REFRESH_EVENT, handler);
  }, []);

  async function fetchTree() {
    try {
      const res = await fetch("/api/graph");
      if (!res.ok) return;
      const data = await res.json();
      const nodes: GraphNode[] = data.nodes;
      const edges: GraphEdgeData[] = data.edges;

      // Build tree from part_of edges
      const nodeMap = new Map<number, TreeNode>();
      for (const n of nodes) {
        nodeMap.set(n.id, { ...n, children: [] });
      }

      // Track which nodes are children
      const childIds = new Set<number>();

      // part_of: source = parent, target = child
      for (const e of edges) {
        if (e.relationship === "part_of") {
          const parent = nodeMap.get(e.source);
          const child = nodeMap.get(e.target);
          if (parent && child && e.source !== e.target) {
            // Only add if child level > parent level (proper nesting)
            if (child.level > parent.level || child.level === 0) {
              // Avoid duplicate children
              if (!parent.children.some((c) => c.id === child.id)) {
                parent.children.push(child);
                childIds.add(child.id);
              }
            }
          }
        }
      }

      // For nodes at level 3 that ended up as direct children of level 1,
      // re-parent them under a level 2 sibling if one shares a part_of edge
      for (const node of nodeMap.values()) {
        if (node.level === 1) {
          const lvl2Kids = node.children.filter((c) => c.level === 2);
          const lvl3Kids = node.children.filter((c) => c.level === 3);

          if (lvl2Kids.length > 0 && lvl3Kids.length > 0) {
            // Move level 3 kids under the last level 2 sibling
            // (simple heuristic — better than flat)
            for (const l3 of lvl3Kids) {
              // Find best level 2 parent by checking depends_on edges
              let bestParent = lvl2Kids[lvl2Kids.length - 1];
              for (const e of edges) {
                if (e.relationship === "depends_on" && e.source === l3.id) {
                  const candidate = lvl2Kids.find((k) => k.id === e.target);
                  if (candidate) { bestParent = candidate; break; }
                }
              }
              if (!bestParent.children.some((c) => c.id === l3.id)) {
                bestParent.children.push(l3);
              }
            }
            // Remove level 3 from direct children of level 1
            node.children = node.children.filter((c) => c.level !== 3);
          }
        }
      }

      // Sort children alphabetically, indexes first
      function sortChildren(node: TreeNode) {
        node.children.sort((a, b) => {
          // Indexes first
          const aIdx = a.title.startsWith("Index:") ? 0 : 1;
          const bIdx = b.title.startsWith("Index:") ? 0 : 1;
          if (aIdx !== bIdx) return aIdx - bIdx;
          return a.title.localeCompare(b.title);
        });
        node.children.forEach(sortChildren);
      }

      // Roots: nodes that aren't children of anyone
      const roots = Array.from(nodeMap.values())
        .filter((n) => !childIds.has(n.id))
        .sort((a, b) => a.title.localeCompare(b.title));

      roots.forEach(sortChildren);

      // Auto-expand level 1 nodes
      const autoExpand = new Set<number>();
      for (const r of roots) {
        if (r.children.length > 0) autoExpand.add(r.id);
      }

      setTree(roots);
      setExpandedIds(autoExpand);
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
    let short = title
      .replace(/^Index:\s*/i, "📑 ")
      .replace(/^Q:\s*/i, "❓ ");
    if (short.length > 45) {
      // Truncate at colon if long
      if (short.includes(": ") && short.indexOf(": ") < 40) {
        short = short.split(": ").slice(1).join(": ");
      }
      if (short.length > 45) {
        short = short.slice(0, 42) + "...";
      }
    }
    return short;
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
          style={{ paddingLeft: `${depth * 14 + 6}px` }}
        >
          {hasChildren ? (
            <button
              onClick={() => toggleExpand(node.id)}
              className="w-4 h-4 flex items-center justify-center flex-shrink-0 text-[10px] text-[var(--muted)]"
            >
              {isExpanded ? "▾" : "▸"}
            </button>
          ) : (
            <span className="w-4 h-4 flex items-center justify-center flex-shrink-0 text-[7px] text-[var(--border)]">
              ●
            </span>
          )}
          <Link
            href={`/notes/${node.slug}`}
            className={`truncate flex-1 ${hasChildren ? "text-[13px] font-medium" : "text-[12.5px]"}`}
            title={node.title}
          >
            {shortenTitle(node.title)}
          </Link>
          {hasChildren && (
            <span className="text-[10px] text-[var(--muted)] flex-shrink-0 pr-1">
              {node.children.length}
            </span>
          )}
        </div>
        {hasChildren && isExpanded && (
          <ul>{node.children.map((child) => renderTreeNode(child, depth + 1))}</ul>
        )}
      </li>
    );
  }

  return (
    <aside className="w-72 h-full flex flex-col bg-[var(--surface)] border-r border-[var(--border)] overflow-hidden flex-shrink-0">
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
            onClick={() => {
              if (pathname !== "/search") {
                window.location.href = "/search";
              }
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                window.location.href = searchQuery.trim()
                  ? `/search?q=${encodeURIComponent(searchQuery)}`
                  : "/search";
              }
            }}
            className="w-full pl-8 pr-3 py-1.5 text-[13px] bg-[var(--bg)] border border-[var(--border)] rounded text-[var(--text)] placeholder-[var(--muted)] focus:outline-none focus:border-[var(--accent-light)] cursor-pointer"
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
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4" />
              </svg>
            </Link>
          </div>
        </div>

        {tree.length === 0 ? (
          <p className="text-[12px] text-[var(--muted)] px-3 py-4">No notes yet.</p>
        ) : (
          <ul className="pb-4">{tree.map((n) => renderTreeNode(n))}</ul>
        )}
      </div>
    </aside>
  );
}
