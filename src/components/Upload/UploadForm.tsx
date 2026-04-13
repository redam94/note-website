"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { useSpace } from "@/contexts/SpaceContext";
import { apiUrl } from "@/lib/api";
import ForceGraph from "@/components/Graph/ForceGraph";
import type { GraphData } from "@/types";

interface DocStatus {
  id: number;
  originalName: string;
  status: string;
  error: string | null;
  processingStep: string | null;
  notesCount: number | null;
  recentNotes: string[];
}

const PIPELINE_STAGES = [
  { key: "upload", label: "Upload" },
  { key: "parse", label: "Parse document" },
  { key: "extract", label: "Extract structure" },
  { key: "outline", label: "Build outline" },
  { key: "plan", label: "Plan notes" },
  { key: "tree", label: "Build topic tree" },
  { key: "create", label: "Create notes" },
  { key: "index", label: "Generate indexes" },
  { key: "links", label: "Insert links" },
  { key: "crosslink", label: "Cross-link" },
  { key: "community", label: "Detect clusters" },
  { key: "done", label: "Complete" },
];

function stageFromStep(step: string | null, status: string): number {
  if (status === "done") return PIPELINE_STAGES.length - 1;
  if (status === "error") return -1;
  if (!step) return 0;
  const s = step.toLowerCase();
  if (s.includes("parsing") || s.includes("parsed")) return 1;
  if (s.includes("extracting") || s.includes("structure extracted") || s.includes("found")) return 2;
  if (s.includes("using extracted") || s.includes("outline") || s.includes("toc")) return 3;
  if (s.includes("planning") || s.includes("planned")) return 4;
  if (s.includes("topic tree") || s.includes("building topic")) return 5;
  if (s.includes("creating note")) return 6;
  if (s.includes("generating index") || s.includes("updating topic index")) return 7;
  if (s.includes("inserting wiki") || s.includes("inserting link")) return 8;
  if (s.includes("cross-link") || s.includes("detecting cross") || s.includes("classifying")) return 9;
  if (s.includes("cluster") || s.includes("community")) return 10;
  return 1;
}

export default function UploadForm() {
  const [isDragging, setIsDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [docStatus, setDocStatus] = useState<DocStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [activityLog, setActivityLog] = useState<string[]>([]);
  const [liveGraph, setLiveGraph] = useState<GraphData | null>(null);
  const [startTime, setStartTime] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const lastStepRef = useRef<string>("");
  const pollRef = useRef<NodeJS.Timeout | null>(null);
  const timerRef = useRef<NodeJS.Timeout | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const graphContainerRef = useRef<HTMLDivElement>(null);
  const [graphWidth, setGraphWidth] = useState(500);
  const spaceRef = useRef<string>("default");
  const router = useRouter();
  const { spaceSlug } = useSpace();
  spaceRef.current = spaceSlug;

  // Elapsed timer
  useEffect(() => {
    if (startTime) {
      timerRef.current = setInterval(() => {
        setElapsed(Math.floor((Date.now() - startTime) / 1000));
      }, 1000);
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [startTime]);

  // Measure graph container
  useEffect(() => {
    function update() {
      if (graphContainerRef.current) {
        setGraphWidth(graphContainerRef.current.clientWidth);
      }
    }
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, [liveGraph]);

  // Auto-scroll log
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [activityLog]);

  // On mount: check for in-progress documents
  useEffect(() => {
    async function checkInProgress() {
      try {
        const res = await fetch(apiUrl("/api/documents", spaceRef.current));
        if (!res.ok) return;
        const docs = await res.json();
        const processing = docs.find(
          (d: any) => d.status === "processing" || d.status === "pending"
        );
        if (processing) {
          setUploading(true);
          setStartTime(Date.now());
          setDocStatus({
            id: processing.id,
            originalName: processing.originalName,
            status: processing.status,
            error: null,
            processingStep: processing.processingStep || "Resuming...",
            notesCount: processing.notesCount,
            recentNotes: processing.recentNotes || [],
          });
          if (processing.processingStep) {
            setActivityLog([processing.processingStep]);
            lastStepRef.current = processing.processingStep;
          }
          startPolling(processing.id);
        }
      } catch { /* ignore */ }
    }
    checkInProgress();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  function startPolling(docId: number) {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const statusRes = await fetch(apiUrl("/api/documents", spaceRef.current));
        if (!statusRes.ok) return;
        const docs = await statusRes.json();
        const current = docs.find((d: any) => d.id === docId);
        if (!current) return;

        const step = current.processingStep;

        // Append new step to activity log
        if (step && step !== lastStepRef.current) {
          lastStepRef.current = step;
          setActivityLog((prev) => [...prev, step]);
        }

        setDocStatus({
          id: current.id,
          originalName: current.originalName,
          status: current.status,
          error: current.error,
          processingStep: step,
          notesCount: current.notesCount,
          recentNotes: current.recentNotes || [],
        });

        // Fetch live graph once notes start appearing
        if ((current.notesCount || 0) >= 1) {
          try {
            const graphRes = await fetch(apiUrl("/api/graph", spaceRef.current));
            if (graphRes.ok) {
              const graphData = await graphRes.json();
              if (graphData.nodes.length > 0) {
                setLiveGraph(graphData);
              }
            }
          } catch { /* ignore graph fetch errors */ }
        }

        if (current.status === "done") {
          if (pollRef.current) clearInterval(pollRef.current);
          if (timerRef.current) clearInterval(timerRef.current);
          setUploading(false);
          setToast(`"${current.originalName}" processed — ${current.notesCount || 0} notes created`);
          setTimeout(() => setToast(null), 5000);
          window.dispatchEvent(new Event("sidebar-refresh"));
          setTimeout(() => router.push("/"), 2500);
        } else if (current.status === "error") {
          if (pollRef.current) clearInterval(pollRef.current);
          if (timerRef.current) clearInterval(timerRef.current);
          setError(current.error || "Processing failed");
          setUploading(false);
        }
      } catch { /* ignore poll errors */ }
    }, 1500);
  }

  const handleFile = useCallback(
    async (file: File) => {
      const allowedExts = [".pdf", ".docx", ".md", ".txt"];
      const ext = "." + file.name.split(".").pop()?.toLowerCase();

      if (!allowedExts.includes(ext)) {
        setError(`Unsupported file type: ${ext}`);
        return;
      }

      setUploading(true);
      setError(null);
      setDocStatus(null);
      setActivityLog(["Uploading file..."]);
      setLiveGraph(null);
      setStartTime(Date.now());
      lastStepRef.current = "";

      try {
        const formData = new FormData();
        formData.append("file", file);

        const res = await fetch(apiUrl("/api/documents", spaceRef.current), {
          method: "POST",
          body: formData,
        });

        if (!res.ok) throw new Error(await res.text());

        const doc = await res.json();
        setActivityLog((prev) => [...prev, "Upload complete. Starting pipeline..."]);
        setDocStatus({
          id: doc.id,
          originalName: doc.originalName,
          status: "processing",
          error: null,
          processingStep: "Starting pipeline...",
          notesCount: null,
          recentNotes: [],
        });

        startPolling(doc.id);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Upload failed");
        setUploading(false);
        setStartTime(null);
      }
    },
    [router]
  );

  const currentStageIdx = docStatus
    ? stageFromStep(docStatus.processingStep, docStatus.status)
    : uploading
      ? 0
      : -1;

  function formatElapsed(s: number): string {
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
  }

  return (
    <div className="max-w-[560px] mx-auto px-4 py-6 md:px-8 md:py-8">
      <h1 className="text-[24px] font-bold text-[var(--heading)] mb-6">
        Upload Document
      </h1>

      {/* Drop zone — hide when processing */}
      {!docStatus && (
        <div
          onDrop={(e) => {
            e.preventDefault();
            setIsDragging(false);
            const file = e.dataTransfer.files[0];
            if (file) handleFile(file);
          }}
          onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
          onDragLeave={(e) => { e.preventDefault(); setIsDragging(false); }}
          className={`border-2 border-dashed rounded-lg p-12 text-center transition-colors ${
            isDragging
              ? "border-[var(--accent)] bg-[var(--accent-bg)]"
              : "border-[var(--border)] hover:border-[var(--muted)]"
          }`}
        >
          <div className="space-y-3">
            <div className="text-3xl">📄</div>
            <p className="text-[14px] text-[var(--text-secondary)]">
              Drag & drop a file here, or{" "}
              <label className="text-[var(--link)] cursor-pointer hover:underline">
                browse
                <input
                  type="file"
                  accept=".pdf,.docx,.md,.txt"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) handleFile(file);
                  }}
                  className="hidden"
                  disabled={uploading}
                />
              </label>
            </p>
            <p className="text-[12px] text-[var(--muted)]">
              PDF, DOCX, Markdown, Plain Text
            </p>
          </div>
        </div>
      )}

      {/* Processing progress */}
      {docStatus && (
        <div className="mt-2 space-y-4">
          {/* Header with file name + elapsed */}
          <div className="bg-[var(--surface)] border border-[var(--border)] rounded-lg p-4">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <span className="text-[15px]">📄</span>
                <span className="text-[14px] font-medium text-[var(--heading)]">
                  {docStatus.originalName}
                </span>
              </div>
              <div className="flex items-center gap-3">
                {docStatus.notesCount != null && docStatus.notesCount > 0 && (
                  <span className="text-[12px] text-[var(--accent)] font-medium">
                    {docStatus.notesCount} notes
                  </span>
                )}
                {startTime && docStatus.status === "processing" && (
                  <span className="text-[11px] text-[var(--muted)] tabular-nums">
                    {formatElapsed(elapsed)}
                  </span>
                )}
              </div>
            </div>

            {/* Pipeline stages — compact */}
            <div className="space-y-0">
              {PIPELINE_STAGES.map((stage, idx) => {
                const isActive = idx === currentStageIdx;
                const isCompleted = currentStageIdx > idx || docStatus.status === "done";
                return (
                  <div key={stage.key} className={`flex items-start gap-3 relative ${isActive ? "animate-pulse" : ""}`}>
                    {idx < PIPELINE_STAGES.length - 1 && (
                      <div
                        className="absolute left-[9px] top-[20px] w-[2px] h-[20px]"
                        style={{ backgroundColor: isCompleted ? "var(--accent)" : "var(--border)" }}
                      />
                    )}
                    <div className="flex-shrink-0 mt-[2px]">
                      {isCompleted ? (
                        <div className="w-5 h-5 rounded-full bg-[var(--accent)] flex items-center justify-center">
                          <svg className="w-3 h-3 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                          </svg>
                        </div>
                      ) : isActive ? (
                        <div className="w-5 h-5 rounded-full border-2 border-[var(--accent)] flex items-center justify-center">
                          <div className="w-2 h-2 rounded-full bg-[var(--accent)]" />
                        </div>
                      ) : (
                        <div className="w-5 h-5 rounded-full border-2 border-[var(--border)]" />
                      )}
                    </div>
                    <div className="pb-3 flex-1 min-w-0">
                      <p className={`text-[13px] leading-tight ${
                        isActive ? "text-[var(--heading)] font-medium" :
                        isCompleted ? "text-[var(--text-secondary)]" :
                        "text-[var(--muted)]"
                      }`}>
                        {stage.label}
                      </p>
                    </div>
                  </div>
                );
              })}
            </div>

            {docStatus.status === "done" && (
              <div className="mt-2 p-3 rounded bg-[var(--accent-bg)] text-[var(--accent)] text-[13px]">
                Processing complete. Redirecting...
              </div>
            )}
          </div>

          {/* Live graph — appears once notes exist */}
          {liveGraph && liveGraph.nodes.length > 0 && (
            <div className="bg-[var(--surface)] border border-[var(--border)] rounded-lg overflow-hidden">
              <div className="px-3 py-2 border-b border-[var(--border)] flex items-center justify-between">
                <span className="text-[11px] font-semibold text-[var(--muted)] uppercase tracking-wider">
                  Knowledge Graph
                </span>
                <span className="text-[10px] text-[var(--muted)]">
                  {liveGraph.nodes.length} nodes · {liveGraph.edges.length} edges
                </span>
              </div>
              <div ref={graphContainerRef}>
                <ForceGraph
                  data={liveGraph}
                  onNodeClick={(slug) => router.push(`/notes/${slug}`)}
                  width={graphWidth}
                  height={200}
                  mode="global"
                  showLegend={false}
                />
              </div>
            </div>
          )}

          {/* Recent notes — fade in as they appear */}
          {docStatus.recentNotes.length > 0 && (
            <div className="bg-[var(--surface)] border border-[var(--border)] rounded-lg p-3">
              <h3 className="text-[11px] font-semibold text-[var(--muted)] uppercase tracking-wider mb-2">
                Notes Created
              </h3>
              <ul className="space-y-1">
                {docStatus.recentNotes.map((title, i) => (
                  <li
                    key={i}
                    className="text-[12px] text-[var(--text-secondary)] flex items-center gap-2 animate-[fadeIn_0.4s_ease-out]"
                  >
                    <span className="w-1.5 h-1.5 rounded-full bg-[var(--accent)] flex-shrink-0" />
                    <span className="truncate">{title}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Activity log */}
          {activityLog.length > 0 && (
            <div className="bg-[var(--surface)] border border-[var(--border)] rounded-lg overflow-hidden">
              <div className="px-3 py-2 border-b border-[var(--border)]">
                <span className="text-[11px] font-semibold text-[var(--muted)] uppercase tracking-wider">
                  Activity Log
                </span>
              </div>
              <div
                ref={logRef}
                className="max-h-[160px] overflow-y-auto p-3 space-y-1"
              >
                {activityLog.map((msg, i) => (
                  <div
                    key={i}
                    className="text-[11px] font-mono text-[var(--text-secondary)] leading-relaxed animate-[fadeIn_0.3s_ease-out]"
                  >
                    <span className="text-[var(--muted)] mr-2 select-none">{'>'}</span>
                    {msg}
                  </div>
                ))}
                {docStatus.status === "processing" && (
                  <div className="text-[11px] font-mono text-[var(--accent)]">
                    <span className="text-[var(--muted)] mr-2 select-none">{'>'}</span>
                    <span className="inline-block w-1.5 h-3 bg-[var(--accent)] animate-pulse" />
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="mt-4 p-3 rounded bg-[var(--danger)]/5 border border-[var(--danger)]/20 text-[13px]">
          <p className="text-[var(--danger)]">{error}</p>
          <button
            onClick={() => { setError(null); setDocStatus(null); setUploading(false); setActivityLog([]); setLiveGraph(null); setStartTime(null); }}
            className="mt-2 px-3 py-1 text-[12px] bg-[var(--surface2)] text-[var(--text-secondary)] rounded hover:bg-[var(--border)] transition-colors"
          >
            Try again
          </button>
        </div>
      )}

      {/* Toast */}
      {toast && (
        <div className="fixed bottom-6 right-6 z-50 max-w-sm px-4 py-3 bg-[var(--accent)] text-white text-[13px] rounded-lg shadow-lg flex items-center gap-3 animate-[slideUp_0.3s_ease-out]">
          <svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
          </svg>
          <span>{toast}</span>
          <button onClick={() => setToast(null)} className="ml-auto flex-shrink-0 opacity-70 hover:opacity-100">
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      )}
    </div>
  );
}
