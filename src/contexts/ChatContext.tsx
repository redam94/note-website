"use client";

/**
 * ChatContext — persists across page navigation.
 *
 * Mounting this at the root layout level means the streaming fetch keeps
 * running even when the user navigates away from /chat.  The ChatInterface
 * page just reads state; it does not own any of it.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { useSpace } from "./SpaceContext";
import { apiUrl } from "@/lib/api";
import type { GraphData } from "@/types";
import type { Message, SourceNote } from "@/components/Chat/ChatMessage";

// ── Streaming token constants ─────────────────────────────────────────
const STATUS_PREFIX  = "<<STATUS>>";
const SOURCES_PREFIX = "<<SOURCES>>";
const GRAPH_PREFIX   = "<<GRAPH>>";

// ── LocalStorage helpers ──────────────────────────────────────────────
function storageKey(spaceSlug: string) {
  return `chat_messages_${spaceSlug}`;
}

function loadMessages(spaceSlug: string): Message[] {
  try {
    const raw = localStorage.getItem(storageKey(spaceSlug));
    if (!raw) return [];
    const parsed: Message[] = JSON.parse(raw);
    return parsed.map((m) => ({ ...m, isStreaming: false }));
  } catch {
    return [];
  }
}

function saveMessages(spaceSlug: string, messages: Message[]) {
  try {
    const trimmed = messages.slice(-200);
    localStorage.setItem(storageKey(spaceSlug), JSON.stringify(trimmed));
  } catch { /* quota / private mode */ }
}

// ── Context shape ─────────────────────────────────────────────────────
export interface ChatContextValue {
  messages: Message[];
  isStreaming: boolean;
  graphData: GraphData | null;
  sendMessage: (question: string) => void;
  clearConversation: () => void;
}

const ChatContext = createContext<ChatContextValue | null>(null);

// ── Provider ──────────────────────────────────────────────────────────
export function ChatProvider({ children }: { children: React.ReactNode }) {
  const { spaceSlug } = useSpace();

  // Initialize empty so server and client render the same HTML (localStorage is
  // client-only). The useEffect below loads persisted messages after hydration.
  const [messages, setMessages]       = useState<Message[]>([]);
  const [isStreaming, setIsStreaming]  = useState(false);
  const [graphData, setGraphData]     = useState<GraphData | null>(null);

  // Stable refs so callbacks don't recreate on every render
  const messagesRef    = useRef(messages);
  const isStreamingRef = useRef(isStreaming);
  const spaceSlugRef   = useRef(spaceSlug);
  messagesRef.current    = messages;
  isStreamingRef.current = isStreaming;
  spaceSlugRef.current   = spaceSlug;

  // AbortController for the in-flight fetch
  const abortRef = useRef<AbortController | null>(null);

  // When space switches: abort any in-flight fetch and switch message history
  useEffect(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setIsStreaming(false);
    setGraphData(null);
    setMessages(loadMessages(spaceSlug));
  }, [spaceSlug]);

  // Persist to localStorage after streaming finishes
  useEffect(() => {
    if (isStreaming) return;
    saveMessages(spaceSlug, messages);
  }, [messages, isStreaming, spaceSlug]);

  // ── sendMessage ──────────────────────────────────────────────────────
  const sendMessage = useCallback((question: string) => {
    const q = question.trim();
    if (!q || isStreamingRef.current) return;

    // Cancel any existing stream
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setIsStreaming(true);
    setGraphData(null);

    // Build history from current messages (strip protocol lines)
    const history = messagesRef.current.map((m) => ({
      role: m.role,
      content:
        m.role === "assistant"
          ? m.content
              .split("\n")
              .filter(
                (l) =>
                  !l.startsWith(STATUS_PREFIX) &&
                  !l.startsWith(SOURCES_PREFIX) &&
                  !l.startsWith(GRAPH_PREFIX)
              )
              .join("\n")
          : m.content,
    }));

    const userMsg: Message      = { role: "user", content: q, timestamp: Date.now() };
    const assistantMsg: Message = {
      role: "assistant",
      content: "",
      sources: [],
      statusMessages: [],
      isStreaming: true,
      timestamp: Date.now(),
    };

    setMessages((prev) => [...prev, userMsg, assistantMsg]);

    // Run stream in background — not awaited, so navigation doesn't kill it
    void (async () => {
      const slug = spaceSlugRef.current;
      try {
        const res = await fetch(apiUrl("/api/chat", slug), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: q, history }),
          signal: controller.signal,
        });

        if (!res.ok) {
          setMessages((prev) => {
            const next = [...prev];
            const last = { ...next[next.length - 1] };
            last.content    = `Error: ${res.status} — ${res.statusText}`;
            last.isStreaming = false;
            next[next.length - 1] = last;
            return next;
          });
          return;
        }

        const reader  = res.body?.getReader();
        const decoder = new TextDecoder();
        if (!reader) return;

        let leftover = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          const text  = leftover + decoder.decode(value, { stream: true });
          leftover    = "";
          const lines = text.split("\n");
          leftover    = lines.pop() ?? "";

          for (const line of lines) {
            if (line.startsWith(STATUS_PREFIX)) {
              const msg = line.slice(STATUS_PREFIX.length).trim();
              if (!msg) continue;
              setMessages((prev) => {
                const next = [...prev];
                const last = { ...next[next.length - 1] };
                last.statusMessages = [...(last.statusMessages ?? []), msg];
                next[next.length - 1] = last;
                return next;
              });
            } else if (line.startsWith(SOURCES_PREFIX)) {
              try {
                const sources: SourceNote[] = JSON.parse(line.slice(SOURCES_PREFIX.length).trim());
                setMessages((prev) => {
                  const next = [...prev];
                  const last = { ...next[next.length - 1] };
                  last.sources = sources;
                  next[next.length - 1] = last;
                  return next;
                });
              } catch { /* ignore parse errors */ }
            } else if (line.startsWith(GRAPH_PREFIX)) {
              try {
                const gd: GraphData = JSON.parse(line.slice(GRAPH_PREFIX.length).trim());
                setGraphData(gd);
              } catch { /* ignore */ }
            } else {
              setMessages((prev) => {
                const next = [...prev];
                const last = { ...next[next.length - 1] };
                last.content = (last.content ?? "") + line + "\n";
                next[next.length - 1] = last;
                return next;
              });
            }
          }

          // Flush partial content immediately — don't wait for the next newline.
          // Protocol tokens always start with "<<", so anything else is safe to
          // stream right away. This makes the LLM response appear word-by-word
          // rather than paragraph-by-paragraph.
          if (leftover && !leftover.startsWith("<<")) {
            setMessages((prev) => {
              const next = [...prev];
              const last = { ...next[next.length - 1] };
              last.content = (last.content ?? "") + leftover;
              next[next.length - 1] = last;
              return next;
            });
            leftover = "";
          }
        }

        // Flush any remaining leftover
        if (
          leftover &&
          !leftover.startsWith(STATUS_PREFIX) &&
          !leftover.startsWith(SOURCES_PREFIX) &&
          !leftover.startsWith(GRAPH_PREFIX)
        ) {
          setMessages((prev) => {
            const next = [...prev];
            const last = { ...next[next.length - 1] };
            last.content = (last.content ?? "") + leftover;
            next[next.length - 1] = last;
            return next;
          });
        }
      } catch (err) {
        if ((err as Error).name === "AbortError") return; // intentional cancel
        setMessages((prev) => {
          const next = [...prev];
          const last = { ...next[next.length - 1] };
          last.content    = "Failed to get a response. Please check your connection and try again.";
          last.isStreaming = false;
          next[next.length - 1] = last;
          return next;
        });
      } finally {
        setMessages((prev) => {
          const next = [...prev];
          const last = { ...next[next.length - 1] };
          if (last.isStreaming) {
            last.isStreaming = false;
            next[next.length - 1] = last;
          }
          return next;
        });
        setIsStreaming(false);
      }
    })();
  }, []); // stable — reads everything from refs or setters

  // ── clearConversation ────────────────────────────────────────────────
  const clearConversation = useCallback(() => {
    if (isStreamingRef.current) return;
    localStorage.removeItem(storageKey(spaceSlugRef.current));
    setMessages([]);
    setGraphData(null);
  }, []);

  return (
    <ChatContext.Provider
      value={{ messages, isStreaming, graphData, sendMessage, clearConversation }}
    >
      {children}
    </ChatContext.Provider>
  );
}

// ── Hook ──────────────────────────────────────────────────────────────
export function useChat(): ChatContextValue {
  const ctx = useContext(ChatContext);
  if (!ctx) throw new Error("useChat must be used within <ChatProvider>");
  return ctx;
}
