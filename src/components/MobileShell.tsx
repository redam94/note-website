"use client";

import { useState, useEffect } from "react";
import { usePathname } from "next/navigation";

interface MobileShellProps {
  sidebar: React.ReactNode;
  children: React.ReactNode;
}

export default function MobileShell({ sidebar, children }: MobileShellProps) {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const pathname = usePathname();

  // Close sidebar on navigation
  useEffect(() => {
    setSidebarOpen(false);
  }, [pathname]);

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Desktop sidebar — always visible */}
      <div className="hidden md:block">
        {sidebar}
      </div>

      {/* Mobile sidebar — overlay drawer */}
      {sidebarOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          {/* Backdrop */}
          <div
            className="absolute inset-0 bg-black/30"
            onClick={() => setSidebarOpen(false)}
          />
          {/* Drawer */}
          <div className="relative z-50 h-full w-[300px] max-w-[85vw]">
            {sidebar}
          </div>
        </div>
      )}

      {/* Main content */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Mobile header bar */}
        <div className="flex md:hidden items-center gap-3 px-3 py-2 border-b border-[var(--border)] bg-[var(--surface)] flex-shrink-0">
          <button
            onClick={() => setSidebarOpen(true)}
            className="p-1.5 text-[var(--text-secondary)] hover:text-[var(--text)] transition-colors"
            aria-label="Open menu"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
          <span className="text-[14px] font-semibold text-[var(--heading)] truncate">
            Second Brain
          </span>
        </div>

        <div className="flex-1 overflow-y-auto">
          {children}
        </div>
      </div>
    </div>
  );
}
