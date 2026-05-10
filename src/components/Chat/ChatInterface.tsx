"use client";

import { useState, useRef, useEffect, useMemo } from "react";
import Link from "next/link";
import { useChat } from "@/contexts/ChatContext";
import { useSpace } from "@/contexts/SpaceContext";
import { apiUrl } from "@/lib/api";
import ChatMessage from "./ChatMessage";
import ResearchGraph from "./ResearchGraph";

function extractFollowUps(content: string): string[] {
  const match = content.match(/##\s*Follow[\s-]*Up\s*Questions?\s*\n([\s\S]*?)(?:\n##|$)/i);
  if (!match) return [];
  const questions: string[] = [];
  for (const line of match[1].split("\n")) {
    const q = line.replace(/^[\s\-\d.]+/, "").trim();
    if (q.length > 10 && q.endsWith("?")) questions.push(q);
  }
  return questions.slice(0, 4);
}

type SavedState =
  | { status: "idle" }
  | { status: "saving" }
  | { status: "saved"; slug: string; title: string }
  | { status: "error"; message: string };

export default function ChatInterface() {
  const { messages, isStreaming, graphData, sendMessage, clearConversation } = useChat();
  const { spaceSlug } = useSpace();
  const [input, setInput] = useState("");
  const [saved, setSaved] = useState<SavedState>({ status: "idle" });
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef  = useRef<HTMLTextAreaElement>(null);

  // Auto-scroll while streaming
  useEffect(() => {
    if (isStreaming) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [messages, isStreaming]);

  // Focus input on mount
  useEffect(() => { inputRef.current?.focus(); }, []);

  function handleSend(questionOverride?: string) {
    const q = (questionOverride ?? input).trim();
    if (!q || isStreaming) return;
    setInput("");
    // Reset textarea height
    if (inputRef.current) inputRef.current.style.height = "auto";
    sendMessage(q);
    setTimeout(() => inputRef.current?.focus(), 50);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  function handleNewConversation() {
    if (isStreaming) return;
    clearConversation();
    setInput("");
    setTimeout(() => inputRef.current?.focus(), 50);
  }

  const lastAssistantIdx = [...messages].map((m, i) => [m, i] as const).reverse().find(([m]) => m.role === "assistant")?.[1];
  const lastAssistant = lastAssistantIdx != null ? messages[lastAssistantIdx] : undefined;
  const lastUserQuestion = useMemo(() => {
    if (lastAssistantIdx == null) return "";
    for (let i = lastAssistantIdx - 1; i >= 0; i--) {
      if (messages[i].role === "user") return messages[i].content;
    }
    return "";
  }, [messages, lastAssistantIdx]);
  const followUps       = lastAssistant && !lastAssistant.isStreaming
    ? extractFollowUps(lastAssistant.content)
    : [];
  const panelSources    = lastAssistant?.sources ?? [];
  const panelStatusMsgs = lastAssistant?.statusMessages ?? [];

  // Reset save state whenever the last assistant message changes (new turn)
  useEffect(() => {
    setSaved({ status: "idle" });
  }, [lastAssistantIdx, lastAssistant?.content]);

  async function handleSaveAnswer() {
    if (!lastAssistant || lastAssistant.isStreaming || !lastUserQuestion) return;
    if (saved.status === "saving" || saved.status === "saved") return;
    setSaved({ status: "saving" });
    try {
      const res = await fetch(apiUrl("/api/ask/save", spaceSlug), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: lastUserQuestion,
          answer: lastAssistant.content,
          tags: [],
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setSaved({ status: "saved", slug: data.slug, title: data.title });
      window.dispatchEvent(new Event("sidebar-refresh"));
    } catch (e) {
      setSaved({ status: "error", message: e instanceof Error ? e.message : "Save failed" });
    }
  }

  // All note IDs that have appeared as sources across the whole conversation
  const allHighlightedIds = useMemo(() => {
    const ids = new Set<number>();
    for (const msg of messages) {
      if (msg.role === "assistant") {
        for (const s of msg.sources ?? []) ids.add(s.id);
      }
    }
    return ids;
  }, [messages]);

  // Note IDs from the most recent query's graph (for labels + extra prominence)
  const latestQueryIds = useMemo(
    () => new Set((graphData?.nodes ?? []).map((n) => n.id)),
    [graphData]
  );

  const isEmpty = messages.length === 0;

  return (
    <div className="flex h-full overflow-hidden">

      {/* ── Left: Chat panel ─────────────────────────────────────────── */}
      <div className="flex flex-col flex-1 min-w-0 h-full">

        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--border)] flex-shrink-0">
          <div>
            <h1 className="text-[18px] font-bold text-[var(--heading)]">Research Assistant</h1>
            <p className="text-[12px] text-[var(--muted)] mt-0.5">
              Ask questions — answers are grounded in your knowledge graph
            </p>
          </div>
          {!isEmpty && (
            <button
              onClick={handleNewConversation}
              disabled={isStreaming}
              className="flex items-center gap-1.5 px-3 py-1.5 text-[12px] font-medium rounded-md border border-[var(--border)] text-[var(--text-secondary)] hover:bg-[var(--surface2)] hover:border-[var(--accent-light)] hover:text-[var(--text)] disabled:opacity-40 transition-all"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
              </svg>
              New conversation
            </button>
          )}
        </div>

        {/* Message list */}
        <div className="flex-1 overflow-y-auto px-6 py-6 min-h-0">
          {isEmpty ? (
            <div className="flex flex-col items-center justify-center h-full text-center max-w-md mx-auto gap-6">
              <div className="w-14 h-14 rounded-2xl bg-[var(--accent-bg)] flex items-center justify-center">
                <svg className="w-7 h-7 text-[var(--accent)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                </svg>
              </div>
              <div>
                <h2 className="text-[16px] font-semibold text-[var(--heading)] mb-2">
                  Ask anything in your knowledge base
                </h2>
                <p className="text-[13px] text-[var(--text-secondary)] leading-relaxed">
                  I can synthesize information across your notes, explain concepts, compare topics,
                  and point you to the source material.
                </p>
              </div>
              <div className="flex flex-col gap-2 w-full">
                {[
                  "Summarize the main topics in this knowledge base",
                  "Explain the key definitions and theorems",
                  "What are the connections between the main concepts?",
                ].map((q) => (
                  <button
                    key={q}
                    onClick={() => handleSend(q)}
                    className="text-left px-4 py-2.5 rounded-lg border border-[var(--border)] text-[13px] text-[var(--text-secondary)] hover:border-[var(--accent-light)] hover:bg-[var(--accent-bg)] hover:text-[var(--accent)] transition-all"
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="max-w-3xl mx-auto">
              {messages.map((msg, i) => (
                <ChatMessage key={i} message={msg} />
              ))}

              {/* Save-as-note action */}
              {lastAssistant && !lastAssistant.isStreaming && lastAssistant.content.trim() && lastUserQuestion && (
                <div className="mt-2 mb-3 flex items-center gap-3">
                  {saved.status === "saved" ? (
                    <Link
                      href={`/notes/${saved.slug}`}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 text-[12px] rounded-md border border-[var(--accent-light)] bg-[var(--accent-bg)] text-[var(--accent)] hover:opacity-90 transition-all"
                    >
                      <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                      </svg>
                      Saved — open note
                    </Link>
                  ) : (
                    <button
                      onClick={handleSaveAnswer}
                      disabled={saved.status === "saving"}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 text-[12px] rounded-md border border-[var(--border)] text-[var(--text-secondary)] hover:bg-[var(--surface2)] hover:border-[var(--accent-light)] hover:text-[var(--text)] disabled:opacity-40 transition-all"
                    >
                      <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 5a2 2 0 012-2h7l5 5v11a2 2 0 01-2 2H7a2 2 0 01-2-2V5z M14 3v5h5" />
                      </svg>
                      {saved.status === "saving" ? "Saving..." : "Save as note"}
                    </button>
                  )}
                  {saved.status === "error" && (
                    <span className="text-[11px] text-[var(--danger)]">{saved.message}</span>
                  )}
                </div>
              )}

              {/* Follow-up chips */}
              {followUps.length > 0 && !isStreaming && (
                <div className="mt-2 mb-4">
                  <p className="text-[11px] font-medium text-[var(--muted)] mb-2 uppercase tracking-wider">
                    Follow-up questions
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {followUps.map((q) => (
                      <button
                        key={q}
                        onClick={() => handleSend(q)}
                        className="px-3 py-1.5 text-[12px] rounded-full border border-[var(--border)] text-[var(--text-secondary)] hover:bg-[var(--accent-bg)] hover:border-[var(--accent-light)] hover:text-[var(--accent)] transition-all text-left"
                      >
                        {q}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {/* Input bar */}
        <div className="flex-shrink-0 border-t border-[var(--border)] px-6 py-4 bg-[var(--bg)]">
          <div className="max-w-3xl mx-auto">
            <div className="relative flex items-end gap-2 bg-[var(--surface)] border border-[var(--border)] rounded-xl focus-within:border-[var(--accent)] focus-within:ring-2 focus-within:ring-[var(--accent)]/20 transition-all">
              <textarea
                ref={inputRef}
                value={input}
                onChange={(e) => {
                  setInput(e.target.value);
                  e.target.style.height = "auto";
                  e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`;
                }}
                onKeyDown={handleKeyDown}
                placeholder="Ask a question... (Enter to send, Shift+Enter for new line)"
                disabled={isStreaming}
                rows={1}
                className="flex-1 resize-none bg-transparent px-4 py-3 text-[14px] text-[var(--text)] placeholder-[var(--muted)] focus:outline-none disabled:opacity-60"
                style={{ minHeight: "44px", maxHeight: "160px" }}
              />
              <button
                onClick={() => handleSend()}
                disabled={!input.trim() || isStreaming}
                className="flex-shrink-0 m-1.5 w-8 h-8 flex items-center justify-center rounded-lg bg-[var(--accent)] text-white hover:bg-[var(--link)] disabled:opacity-40 disabled:cursor-not-allowed transition-all"
                aria-label="Send"
              >
                {isStreaming ? (
                  <span className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                ) : (
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
                  </svg>
                )}
              </button>
            </div>
            <p className="text-[10px] text-[var(--muted)] mt-1.5 text-center">
              Answers are grounded in your knowledge base · Shift+Enter for new line
            </p>
          </div>
        </div>
      </div>

      {/* ── Right: Research graph panel (hidden on small screens) ────── */}
      <div className="hidden lg:flex flex-col w-[340px] xl:w-[400px] flex-shrink-0 border-l border-[var(--border)] h-full overflow-hidden">
        <ResearchGraph
          highlightedIds={allHighlightedIds}
          latestQueryIds={latestQueryIds}
          isSearching={isStreaming}
          sources={panelSources}
          statusMessages={panelStatusMsgs}
        />
      </div>

    </div>
  );
}
