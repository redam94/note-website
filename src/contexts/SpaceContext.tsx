"use client";

import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  type ReactNode,
} from "react";
import type { Space } from "@/types";

interface SpaceContextValue {
  spaceSlug: string;
  spaces: Space[];
  setSpace: (slug: string) => void;
  refreshSpaces: () => Promise<void>;
}

const SpaceContext = createContext<SpaceContextValue>({
  spaceSlug: "default",
  spaces: [],
  setSpace: () => {},
  refreshSpaces: async () => {},
});

const STORAGE_KEY = "selected-space";

export function SpaceProvider({ children }: { children: ReactNode }) {
  const [spaceSlug, setSpaceSlug] = useState<string>(() => {
    if (typeof window !== "undefined") {
      return localStorage.getItem(STORAGE_KEY) || "default";
    }
    return "default";
  });
  const [spaces, setSpaces] = useState<Space[]>([]);

  const refreshSpaces = useCallback(async () => {
    try {
      const r = await fetch("/api/spaces");
      if (!r.ok) return;
      const data: Space[] = await r.json();
      setSpaces(data);
      if (data.length > 0 && !data.some((s) => s.slug === spaceSlug)) {
        const fallback = data.find((s) => s.slug === "default")?.slug || data[0].slug;
        setSpaceSlug(fallback);
        localStorage.setItem(STORAGE_KEY, fallback);
      }
    } catch {}
  }, [spaceSlug]);

  useEffect(() => {
    refreshSpaces();
  }, []);

  const setSpace = useCallback((slug: string) => {
    setSpaceSlug(slug);
    localStorage.setItem(STORAGE_KEY, slug);
  }, []);

  return (
    <SpaceContext.Provider value={{ spaceSlug, spaces, setSpace, refreshSpaces }}>
      {children}
    </SpaceContext.Provider>
  );
}

export function useSpace() {
  return useContext(SpaceContext);
}
