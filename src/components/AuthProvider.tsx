"use client";

import { createContext, useContext, useEffect, useState, useCallback } from "react";

interface AuthState {
  role: "admin" | "user" | "guest";
  hasApiKey: boolean;
  loading: boolean;
  login: (role: string, password?: string, apiKey?: string) => Promise<boolean>;
  logout: () => Promise<void>;
  updateApiKey: (key: string) => Promise<void>;
}

const AuthContext = createContext<AuthState>({
  role: "guest",
  hasApiKey: false,
  loading: true,
  login: async () => false,
  logout: async () => {},
  updateApiKey: async () => {},
});

export function useAuth() {
  return useContext(AuthContext);
}

export default function AuthProvider({ children }: { children: React.ReactNode }) {
  const [role, setRole] = useState<"admin" | "user" | "guest">("guest");
  const [hasApiKey, setHasApiKey] = useState(false);
  const [loading, setLoading] = useState(true);

  // Check session on mount
  useEffect(() => {
    fetch("/api/auth/me")
      .then((r) => (r.ok ? r.json() : { role: "guest", has_api_key: false }))
      .then((data) => {
        setRole(data.role || "guest");
        setHasApiKey(data.has_api_key || false);
      })
      .catch(() => {
        setRole("guest");
        setHasApiKey(false);
      })
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (loginRole: string, password?: string, apiKey?: string) => {
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role: loginRole, password, api_key: apiKey }),
      });
      const data = await res.json();
      if (data.error) return false;
      setRole(data.role || "guest");
      setHasApiKey(data.has_api_key || false);
      return true;
    } catch {
      return false;
    }
  }, []);

  const logout = useCallback(async () => {
    await fetch("/api/auth/logout", { method: "POST" });
    setRole("guest");
    setHasApiKey(false);
  }, []);

  const updateApiKey = useCallback(async (key: string) => {
    try {
      const res = await fetch("/api/auth/api-key", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: key }),
      });
      const data = await res.json();
      setRole(data.role || "user");
      setHasApiKey(data.has_api_key || false);
    } catch { /* ignore */ }
  }, []);

  return (
    <AuthContext.Provider value={{ role, hasApiKey, loading, login, logout, updateApiKey }}>
      {children}
    </AuthContext.Provider>
  );
}
