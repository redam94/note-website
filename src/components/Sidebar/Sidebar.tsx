"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/components/AuthProvider";
import { useSpace } from "@/contexts/SpaceContext";
import { apiUrl } from "@/lib/api";
import type { GraphNode, GraphEdgeData, Space } from "@/types";

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
  const pathnameRef = useRef(pathname);
  pathnameRef.current = pathname;
  const router = useRouter();
  const { role, logout } = useAuth();
  const { spaceSlug, spaces, setSpace, refreshSpaces } = useSpace();
  const spaceSlugRef = useRef(spaceSlug);
  spaceSlugRef.current = spaceSlug;
  const isAdmin = role === "admin";
  const [showNewSpace, setShowNewSpace] = useState(false);
  const [newSpaceName, setNewSpaceName] = useState("");

  // Stable fetchTree — reads spaceSlug/pathname from refs so it never goes stale
  const fetchTree = useCallback(async () => {
    try {
      const res = await fetch(apiUrl("/api/graph", spaceSlugRef.current));
      if (!res.ok) return;
      const data = await res.json();
      const nodes: GraphNode[] = data.nodes;
      const edges: GraphEdgeData[] = data.edges;

      const nodeMap = new Map<number, TreeNode>();
      for (const n of nodes) {
        nodeMap.set(n.id, { ...n, children: [] });
      }

      const childIds = new Set<number>();
      for (const e of edges) {
        if (e.relationship === "part_of") {
          const parent = nodeMap.get(e.source);
          const child = nodeMap.get(e.target);
          if (parent && child && e.source !== e.target) {
            if (child.id !== parent.id) {
              if (!parent.children.some((c) => c.id === child.id)) {
                parent.children.push(child);
                childIds.add(child.id);
              }
            }
          }
        }
      }

      for (const node of nodeMap.values()) {
        if (node.level === 1) {
          const lvl2Kids = node.children.filter((c) => c.level === 2);
          const lvl3Kids = node.children.filter((c) => c.level === 3);
          if (lvl2Kids.length > 0 && lvl3Kids.length > 0) {
            for (const l3 of lvl3Kids) {
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
            node.children = node.children.filter((c) => c.level !== 3);
          }
        }
      }

      const visited = new Set<number>();
      function sortChildren(node: TreeNode) {
        if (visited.has(node.id)) return; // cycle guard
        visited.add(node.id);
        node.children.sort((a, b) => {
          const aIdx = a.title.startsWith("Index:") ? 0 : 1;
          const bIdx = b.title.startsWith("Index:") ? 0 : 1;
          if (aIdx !== bIdx) return aIdx - bIdx;
          return a.title.localeCompare(b.title);
        });
        node.children.forEach(sortChildren);
        visited.delete(node.id);
      }

      const roots = Array.from(nodeMap.values())
        .filter((n) => !childIds.has(n.id))
        .sort((a, b) => a.title.localeCompare(b.title));
      roots.forEach(sortChildren);

      const parentOf = new Map<number, number>();
      function mapParents(node: TreeNode) {
        for (const child of node.children) {
          parentOf.set(child.id, node.id);
          mapParents(child);
        }
      }
      roots.forEach(mapParents);

      function getAncestorIds(slug: string): Set<number> {
        const ids = new Set<number>();
        for (const n of nodeMap.values()) {
          if (n.slug === slug) {
            let cur = n.id;
            while (parentOf.has(cur)) {
              cur = parentOf.get(cur)!;
              ids.add(cur);
            }
            break;
          }
        }
        return ids;
      }

      const currentSlug = pathnameRef.current?.startsWith("/notes/")
        ? pathnameRef.current.slice(7)
        : "";
      const ancestorIds = currentSlug ? getAncestorIds(currentSlug) : new Set<number>();

      setTree(roots);
      setExpandedIds((prev) => {
        const next = new Set(prev);
        if (prev.size === 0) {
          for (const r of roots) {
            if (r.children.length > 0) next.add(r.id);
          }
        }
        for (const id of ancestorIds) next.add(id);
        return next;
      });
    } catch { /* silent */ }
  }, []); // stable — reads from refs

  // When pathname changes, expand ancestors of the active note
  useEffect(() => {
    if (prevPathRef.current !== pathname) {
      prevPathRef.current = pathname;
      fetchTree();
    }
  }, [pathname, fetchTree]);

  // Expand ancestors of the currently viewed note whenever pathname changes
  useEffect(() => {
    const currentSlug = pathname?.startsWith("/notes/") ? pathname.slice(7) : "";
    if (!currentSlug || tree.length === 0) return;

    // Build parent map from current tree
    const parentOf = new Map<number, number>();
    function walkTree(node: TreeNode) {
      for (const child of node.children) {
        parentOf.set(child.id, node.id);
        walkTree(child);
      }
    }
    tree.forEach(walkTree);

    // Find the node and expand its ancestors
    function findAndExpand(nodes: TreeNode[]): boolean {
      for (const n of nodes) {
        if (n.slug === currentSlug) {
          const toExpand: number[] = [];
          let cur = n.id;
          while (parentOf.has(cur)) {
            cur = parentOf.get(cur)!;
            toExpand.push(cur);
          }
          if (toExpand.length > 0) {
            setExpandedIds((prev) => {
              const next = new Set(prev);
              for (const id of toExpand) next.add(id);
              return next;
            });
          }
          return true;
        }
        if (findAndExpand(n.children)) return true;
      }
      return false;
    }
    findAndExpand(tree);
  }, [pathname, tree]);

  // Re-fetch tree when space changes; clear ghost-expanded nodes from previous space
  useEffect(() => {
    setExpandedIds(new Set());
    fetchTree();
    window.addEventListener(REFRESH_EVENT, fetchTree);
    return () => window.removeEventListener(REFRESH_EVENT, fetchTree);
  }, [spaceSlug, fetchTree]);

  function toggleExpand(id: number) {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  function shortenTitle(title: string): string {
    // Index notes: strip "Index: " prefix and show only the leaf folder name
    if (/^Index:\s*/i.test(title)) {
      const path = title.replace(/^Index:\s*/i, "");
      const leaf = path.includes("/") ? path.split("/").pop()! : path;
      return `📑 ${leaf}`;
    }

    let short = title.replace(/^Q:\s*/i, "❓ ");
    if (short.length > 45) {
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
    <aside className="w-[300px] h-full flex flex-col bg-[var(--surface)] border-r border-[var(--border)] overflow-hidden flex-shrink-0">
      {/* Space selector */}
      <div className="px-3 pt-3 pb-0">
        <div className="flex items-center gap-1">
          <select
            value={spaceSlug}
            onChange={(e) => { setSpace(e.target.value); router.push("/"); }}
            className="flex-1 min-w-0 text-[12px] px-2 py-1 bg-[var(--bg)] border border-[var(--border)] rounded text-[var(--text-secondary)] focus:outline-none focus:border-[var(--accent-light)] transition-colors cursor-pointer"
          >
            {spaces.map((s) => (
              <option key={s.slug} value={s.slug}>
                {s.name}
              </option>
            ))}
          </select>
          {isAdmin && (
            <>
              <button
                onClick={() => setShowNewSpace(!showNewSpace)}
                className="flex-shrink-0 w-6 h-6 flex items-center justify-center text-[var(--muted)] hover:text-[var(--accent)] transition-colors rounded hover:bg-[var(--surface2)]"
                title="New space"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                </svg>
              </button>
              {spaceSlug !== "default" && (
                <button
                  onClick={async () => {
                    if (!confirm(`Delete space "${spaces.find(s => s.slug === spaceSlug)?.name}"? All its notes will be permanently deleted.`)) return;
                    const res = await fetch(`/api/spaces/${spaceSlug}`, { method: "DELETE" });
                    if (res.ok) {
                      setSpace("default");
                      await refreshSpaces();
                    }
                  }}
                  className="flex-shrink-0 w-6 h-6 flex items-center justify-center text-[var(--muted)] hover:text-[var(--danger)] transition-colors rounded hover:bg-[var(--surface2)]"
                  title="Delete space"
                >
                  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                  </svg>
                </button>
              )}
            </>
          )}
        </div>
        {showNewSpace && isAdmin && (
          <form
            className="mt-2 flex gap-1"
            onSubmit={async (e) => {
              e.preventDefault();
              const name = newSpaceName.trim();
              if (!name) return;
              const res = await fetch("/api/spaces", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name }),
              });
              if (res.ok) {
                const created = await res.json();
                await refreshSpaces();
                setSpace(created.slug);
                setNewSpaceName("");
                setShowNewSpace(false);
              }
            }}
          >
            <input
              type="text"
              value={newSpaceName}
              onChange={(e) => setNewSpaceName(e.target.value)}
              placeholder="Space name..."
              autoFocus
              className="flex-1 min-w-0 text-[12px] px-2 py-1 bg-[var(--bg)] border border-[var(--border)] rounded text-[var(--text)] placeholder-[var(--muted)] focus:outline-none focus:border-[var(--accent-light)]"
            />
            <button
              type="submit"
              className="flex-shrink-0 px-2 py-1 text-[11px] font-medium bg-[var(--accent)] text-white rounded hover:opacity-90 transition-opacity"
            >
              Create
            </button>
          </form>
        )}
      </div>

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

      {/* Chat link */}
      <div className="px-3 pb-2">
        <Link
          href="/chat"
          className={`flex items-center gap-2 px-3 py-2 rounded-lg text-[13px] font-medium transition-all ${
            pathname === "/chat"
              ? "bg-[var(--accent-bg-hover)] text-[var(--accent)]"
              : "text-[var(--text-secondary)] hover:bg-[var(--surface2)] hover:text-[var(--text)]"
          }`}
        >
          <svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
          </svg>
          Research Assistant
        </Link>
      </div>

      {/* Explorer */}
      <div className="flex-1 overflow-y-auto px-1">
        <div className="px-2 py-1.5 flex items-center justify-between">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-[var(--muted)]">
            Explorer
          </span>
          <div className="flex gap-1">
            {isAdmin && (
              <>
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
              </>
            )}
          </div>
        </div>

        {tree.length === 0 ? (
          <p className="text-[12px] text-[var(--muted)] px-3 py-4">No notes yet.</p>
        ) : (
          <ul className="pb-4">{tree.map((n) => renderTreeNode(n))}</ul>
        )}
      </div>

      {/* Auth status */}
      <div className="px-3 py-2 border-t border-[var(--border)] flex items-center justify-between">
        <span className="text-[11px] text-[var(--muted)]">
          {role === "admin" ? "Admin" : role === "user" ? "Reader" : "Guest"}
        </span>
        {role !== "guest" ? (
          <button
            onClick={() => { logout(); window.location.href = "/login"; }}
            className="text-[11px] text-[var(--muted)] hover:text-[var(--text)] transition-colors"
          >
            Sign out
          </button>
        ) : (
          <Link href="/login" className="text-[11px] text-[var(--link)] hover:underline">
            Sign in
          </Link>
        )}
      </div>
    </aside>
  );
}
