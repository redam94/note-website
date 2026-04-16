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
  if (
    s.includes("cross-link") ||
    s.includes("detecting cross") ||
    s.includes("classifying") ||
    s.includes("waiting for cross")
  ) return 9;
  if (s.includes("cluster") || s.includes("community")) return 10;
  return 1;
}

// ── Single-file upload (chunked) ─────────────────────────────────────────────

async function uploadFileChunked(
  file: File,
  spaceSlug: string,
  onProgress: (pct: number) => void,
  deferProcessing: boolean,
): Promise<{ id: number; originalName: string }> {
  const CHUNK_SIZE = 8 * 1024 * 1024;
  const sessionId = crypto.randomUUID();
  const totalChunks = Math.ceil(file.size / CHUNK_SIZE);

  for (let i = 0; i < totalChunks; i++) {
    const start = i * CHUNK_SIZE;
    const chunkBlob = file.slice(start, start + CHUNK_SIZE);
    const form = new FormData();
    form.append("session_id", sessionId);
    form.append("chunk_index", String(i));
    form.append("total_chunks", String(totalChunks));
    form.append("filename", file.name);
    form.append("chunk", chunkBlob, file.name);

    const res = await fetch(apiUrl("/api/documents/upload-chunk", spaceSlug), {
      method: "POST",
      body: form,
    });
    if (!res.ok) throw new Error(await res.text());
    onProgress(Math.round(((i + 1) / totalChunks) * 100));
  }

  const completeForm = new FormData();
  completeForm.append("session_id", sessionId);
  completeForm.append("filename", file.name);
  if (deferProcessing) completeForm.append("defer_processing", "true");

  const res = await fetch(apiUrl("/api/documents/upload-complete", spaceSlug), {
    method: "POST",
    body: completeForm,
  });
  if (!res.ok) throw new Error(await res.text());
  const doc = await res.json();
  return { id: doc.id, originalName: doc.originalName };
}

// ── Shared helpers ────────────────────────────────────────────────────────────

function formatElapsed(s: number): string {
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
}

// ── Main component ────────────────────────────────────────────────────────────

export default function UploadForm() {
  const [isDragging, setIsDragging] = useState(false);
  const [mode, setMode] = useState<"idle" | "single" | "batch">("idle");

  // Single-file state
  const [uploading, setUploading] = useState(false);
  const [uploadPct, setUploadPct] = useState(0);
  const [uploadFileName, setUploadFileName] = useState<string>("");
  const [docStatus, setDocStatus] = useState<DocStatus | null>(null);
  const [activityLog, setActivityLog] = useState<string[]>([]);
  const [liveGraph, setLiveGraph] = useState<GraphData | null>(null);
  const [startTime, setStartTime] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [stubs, setStubs] = useState<{ id: number; title: string; slug: string; reason: string }[]>([]);
  const [repairState, setRepairState] = useState<"idle" | "repairing" | "done">("idle");
  const [repairResults, setRepairResults] = useState<{ slug: string; title: string; success: boolean; error?: string }[]>([]);

  // Batch state
  const [batchUploading, setBatchUploading] = useState(false);
  const [batchUploadProgress, setBatchUploadProgress] = useState<{ name: string; pct: number }[]>([]);
  const [batchDocs, setBatchDocs] = useState<DocStatus[]>([]);
  const [batchStartTime, setBatchStartTime] = useState<number | null>(null);
  const [batchElapsed, setBatchElapsed] = useState(0);
  const [batchPhase, setBatchPhase] = useState<"uploading" | "processing" | "done" | null>(null);

  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const lastStepRef = useRef<string>("");
  const pollRef = useRef<NodeJS.Timeout | null>(null);
  const batchPollRef = useRef<NodeJS.Timeout | null>(null);
  const timerRef = useRef<NodeJS.Timeout | null>(null);
  const batchTimerRef = useRef<NodeJS.Timeout | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const graphContainerRef = useRef<HTMLDivElement>(null);
  const [graphWidth, setGraphWidth] = useState(500);
  const spaceRef = useRef<string>("default");
  const router = useRouter();
  const { spaceSlug } = useSpace();
  spaceRef.current = spaceSlug;

  // Timers
  useEffect(() => {
    if (startTime) {
      timerRef.current = setInterval(() => setElapsed(Math.floor((Date.now() - startTime) / 1000)), 1000);
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [startTime]);

  useEffect(() => {
    if (batchStartTime) {
      batchTimerRef.current = setInterval(() => setBatchElapsed(Math.floor((Date.now() - batchStartTime) / 1000)), 1000);
    }
    return () => { if (batchTimerRef.current) clearInterval(batchTimerRef.current); };
  }, [batchStartTime]);

  // Graph container width
  useEffect(() => {
    function update() {
      if (graphContainerRef.current) setGraphWidth(graphContainerRef.current.clientWidth);
    }
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, [liveGraph]);

  // Auto-scroll log
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [activityLog]);

  // On mount: check for in-progress documents
  useEffect(() => {
    async function checkInProgress() {
      try {
        const res = await fetch(apiUrl("/api/documents", spaceRef.current));
        if (!res.ok) return;
        const docs = await res.json();
        const processing = docs.find((d: any) => d.status === "processing" || d.status === "pending");
        if (processing) {
          setMode("single");
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
          startSinglePoll(processing.id);
        }
      } catch { /* ignore */ }
    }
    checkInProgress();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      if (batchPollRef.current) clearInterval(batchPollRef.current);
      if (timerRef.current) clearInterval(timerRef.current);
      if (batchTimerRef.current) clearInterval(batchTimerRef.current);
    };
  }, []);

  // ── Single-file polling ──────────────────────────────────────────────────

  function startSinglePoll(docId: number) {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const res = await fetch(apiUrl("/api/documents", spaceRef.current));
        if (!res.ok) return;
        const docs = await res.json();
        const current = docs.find((d: any) => d.id === docId);
        if (!current) return;

        const step = current.processingStep;
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

        if ((current.notesCount || 0) >= 1) {
          try {
            const gRes = await fetch(apiUrl("/api/graph", spaceRef.current));
            if (gRes.ok) {
              const g = await gRes.json();
              if (g.nodes.length > 0) setLiveGraph(g);
            }
          } catch { /* ignore */ }
        }

        if (current.status === "done") {
          if (pollRef.current) clearInterval(pollRef.current);
          if (timerRef.current) clearInterval(timerRef.current);
          setUploading(false);
          window.dispatchEvent(new Event("sidebar-refresh"));

          try {
            const stubRes = await fetch(apiUrl(`/api/documents/${current.id}/stubs`, spaceRef.current));
            if (stubRes.ok) {
              const stubList = await stubRes.json();
              if (stubList.length > 0) { setStubs(stubList); return; }
            }
          } catch { /* ignore */ }

          setToast(`"${current.originalName}" processed — ${current.notesCount || 0} notes created`);
          setTimeout(() => setToast(null), 5000);
          setTimeout(() => router.push("/"), 2500);
        } else if (current.status === "error") {
          if (pollRef.current) clearInterval(pollRef.current);
          if (timerRef.current) clearInterval(timerRef.current);
          setError(current.error || "Processing failed");
          setUploading(false);
        }
      } catch { /* ignore */ }
    }, 1500);
  }

  // ── Batch polling ────────────────────────────────────────────────────────

  function startBatchPoll(docIds: number[]) {
    if (batchPollRef.current) clearInterval(batchPollRef.current);
    batchPollRef.current = setInterval(async () => {
      try {
        const res = await fetch(apiUrl("/api/documents", spaceRef.current));
        if (!res.ok) return;
        const allDocs = await res.json();

        const updated: DocStatus[] = docIds.map((id) => {
          const d = allDocs.find((x: any) => x.id === id);
          if (!d) return { id, originalName: "...", status: "pending", error: null, processingStep: null, notesCount: null, recentNotes: [] };
          return {
            id: d.id,
            originalName: d.originalName,
            status: d.status,
            error: d.error,
            processingStep: d.processingStep,
            notesCount: d.notesCount,
            recentNotes: d.recentNotes || [],
          };
        });

        setBatchDocs(updated);

        const allDone = updated.every((d) => d.status === "done" || d.status === "error");
        if (allDone) {
          if (batchPollRef.current) clearInterval(batchPollRef.current);
          if (batchTimerRef.current) clearInterval(batchTimerRef.current);
          setBatchPhase("done");
          setBatchUploading(false);
          window.dispatchEvent(new Event("sidebar-refresh"));

          const totalNotes = updated.reduce((sum, d) => sum + (d.notesCount || 0), 0);
          const errored = updated.filter((d) => d.status === "error").length;
          const msg = errored
            ? `${updated.length - errored}/${updated.length} documents processed — ${totalNotes} notes`
            : `${updated.length} documents processed — ${totalNotes} notes`;
          setToast(msg);
          setTimeout(() => setToast(null), 6000);
          setTimeout(() => router.push("/"), 3000);
        }
      } catch { /* ignore */ }
    }, 1500);
  }

  // ── File handlers ────────────────────────────────────────────────────────

  const handleFiles = useCallback(
    async (files: File[]) => {
      if (files.length === 0) return;
      setError(null);

      if (files.length === 1) {
        // Single-file flow (chunked, with repair, activity log, graph)
        setMode("single");
        setUploading(true);
        setUploadPct(0);
        setUploadFileName(files[0].name);
        setDocStatus(null);
        setActivityLog([]);
        setLiveGraph(null);
        setStartTime(Date.now());
        lastStepRef.current = "";

        try {
          const doc = await uploadFileChunked(
            files[0],
            spaceRef.current,
            (pct) => setUploadPct(pct),
            false,
          );
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
          startSinglePoll(doc.id);
        } catch (err) {
          setError(err instanceof Error ? err.message : "Upload failed");
          setUploading(false);
          setStartTime(null);
          setMode("idle");
        }
      } else {
        // Batch flow: upload files sequentially with defer, then finalize
        setMode("batch");
        setBatchUploading(true);
        setBatchPhase("uploading");
        setBatchStartTime(Date.now());
        setBatchDocs([]);
        setBatchUploadProgress(files.map((f) => ({ name: f.name, pct: 0 })));

        try {
          const docIds: number[] = [];

          for (let i = 0; i < files.length; i++) {
            const file = files[i];
            const doc = await uploadFileChunked(
              file,
              spaceRef.current,
              (pct) => {
                setBatchUploadProgress((prev) => {
                  const next = [...prev];
                  next[i] = { name: file.name, pct };
                  return next;
                });
              },
              true, // defer_processing
            );
            docIds.push(doc.id);
            // Show deferred docs as "pending" immediately
            setBatchDocs((prev) => [
              ...prev,
              { id: doc.id, originalName: doc.originalName, status: "pending", error: null, processingStep: null, notesCount: null, recentNotes: [] },
            ]);
          }

          // Kick off batch processing
          setBatchPhase("processing");
          const finalizeRes = await fetch(
            apiUrl("/api/documents/batch-finalize", spaceRef.current),
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ doc_ids: docIds }),
            },
          );
          if (!finalizeRes.ok) throw new Error(await finalizeRes.text());

          startBatchPoll(docIds);
        } catch (err) {
          setError(err instanceof Error ? err.message : "Batch upload failed");
          setBatchUploading(false);
          setBatchPhase(null);
          setMode("idle");
        }
      }
    },
    [router],
  );

  const handleFile = useCallback(
    (file: File) => handleFiles([file]),
    [handleFiles],
  );

  async function handleRepairStubs() {
    if (!docStatus) return;
    setRepairState("repairing");
    try {
      const res = await fetch(apiUrl(`/api/documents/${docStatus.id}/repair-stubs`, spaceSlug), { method: "POST" });
      if (!res.ok) throw new Error(await res.text());
      const results = await res.json();
      setRepairResults(results);
      setRepairState("done");
      window.dispatchEvent(new Event("sidebar-refresh"));
    } catch (e) {
      setRepairState("done");
      setRepairResults([{ slug: "", title: "Repair request failed", success: false, error: String(e) }]);
    }
  }

  function resetToIdle() {
    setMode("idle");
    setError(null);
    setDocStatus(null);
    setUploading(false);
    setUploadPct(0);
    setUploadFileName("");
    setActivityLog([]);
    setLiveGraph(null);
    setStartTime(null);
    setStubs([]);
    setRepairState("idle");
    setRepairResults([]);
    setBatchUploading(false);
    setBatchDocs([]);
    setBatchUploadProgress([]);
    setBatchPhase(null);
    setBatchStartTime(null);
  }

  const currentStageIdx = docStatus
    ? stageFromStep(docStatus.processingStep, docStatus.status)
    : uploading ? 0 : -1;

  const showDropZone = mode === "idle";

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <div className="max-w-[560px] mx-auto px-4 py-6 md:px-8 md:py-8">
      <h1 className="text-[24px] font-bold text-[var(--heading)] mb-6">Upload Documents</h1>

      {/* Drop zone */}
      {showDropZone && (
        <div
          onDrop={(e) => {
            e.preventDefault();
            setIsDragging(false);
            const files = Array.from(e.dataTransfer.files);
            if (files.length > 0) handleFiles(files);
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
              Drag & drop files here, or{" "}
              <label className="text-[var(--link)] cursor-pointer hover:underline">
                browse
                <input
                  type="file"
                  multiple
                  onChange={(e) => {
                    const files = Array.from(e.target.files || []);
                    if (files.length > 0) handleFiles(files);
                  }}
                  className="hidden"
                />
              </label>
            </p>
            <p className="text-[12px] text-[var(--muted)]">
              PDF, DOCX, Markdown, Plain Text · select multiple for batch processing
            </p>
          </div>
        </div>
      )}

      {/* Single-file: chunk upload progress */}
      {mode === "single" && uploading && !docStatus && (
        <div className="bg-[var(--surface)] border border-[var(--border)] rounded-lg p-4 space-y-3">
          <div className="flex items-center gap-2">
            <span className="text-[15px]">📄</span>
            <span className="text-[14px] font-medium text-[var(--heading)] truncate flex-1">{uploadFileName}</span>
            <span className="text-[12px] text-[var(--muted)] tabular-nums flex-shrink-0">{uploadPct}%</span>
          </div>
          <div className="w-full h-1.5 bg-[var(--border)] rounded-full overflow-hidden">
            <div className="h-full bg-[var(--accent)] rounded-full transition-all duration-300" style={{ width: `${uploadPct}%` }} />
          </div>
          <p className="text-[12px] text-[var(--muted)]">Uploading{uploadPct < 100 ? "..." : " — finalizing..."}</p>
        </div>
      )}

      {/* Single-file: processing progress */}
      {mode === "single" && docStatus && (
        <div className="mt-2 space-y-4">
          <div className="bg-[var(--surface)] border border-[var(--border)] rounded-lg p-4">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <span className="text-[15px]">📄</span>
                <span className="text-[14px] font-medium text-[var(--heading)]">{docStatus.originalName}</span>
              </div>
              <div className="flex items-center gap-3">
                {docStatus.notesCount != null && docStatus.notesCount > 0 && (
                  <span className="text-[12px] text-[var(--accent)] font-medium">{docStatus.notesCount} notes</span>
                )}
                {startTime && docStatus.status === "processing" && (
                  <span className="text-[11px] text-[var(--muted)] tabular-nums">{formatElapsed(elapsed)}</span>
                )}
              </div>
            </div>

            <div className="space-y-0">
              {PIPELINE_STAGES.map((stage, idx) => {
                const isActive = idx === currentStageIdx;
                const isCompleted = currentStageIdx > idx || docStatus.status === "done";
                return (
                  <div key={stage.key} className={`flex items-start gap-3 relative ${isActive ? "animate-pulse" : ""}`}>
                    {idx < PIPELINE_STAGES.length - 1 && (
                      <div className="absolute left-[9px] top-[20px] w-[2px] h-[20px]"
                        style={{ backgroundColor: isCompleted ? "var(--accent)" : "var(--border)" }} />
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
                      }`}>{stage.label}</p>
                    </div>
                  </div>
                );
              })}
            </div>

            {docStatus.status === "done" && stubs.length === 0 && repairState === "idle" && (
              <div className="mt-2 p-3 rounded bg-[var(--accent-bg)] text-[var(--accent)] text-[13px]">
                Processing complete. Redirecting...
              </div>
            )}
          </div>

          {/* Stub repair */}
          {stubs.length > 0 && repairState !== "done" && (
            <div className="bg-[var(--surface)] border border-amber-400/40 rounded-lg p-4 space-y-3">
              <div className="flex items-start gap-3">
                <span className="text-amber-400 text-[18px] flex-shrink-0">⚠</span>
                <div className="flex-1 min-w-0">
                  <p className="text-[13px] font-semibold text-[var(--heading)]">
                    {stubs.length} note{stubs.length !== 1 ? "s" : ""} generated as stubs
                  </p>
                  <p className="text-[12px] text-[var(--muted)] mt-0.5">
                    The AI couldn&apos;t generate full content for these notes. Repair them now or skip and fix later.
                  </p>
                </div>
              </div>
              <ul className="space-y-1 max-h-[140px] overflow-y-auto">
                {stubs.map((s) => (
                  <li key={s.slug} className="flex items-start gap-2 text-[11px]">
                    <span className="text-amber-400 flex-shrink-0 mt-0.5">•</span>
                    <span className="font-medium text-[var(--text-secondary)] truncate">{s.title}</span>
                    <span className="text-[var(--muted)] flex-shrink-0 ml-auto pl-2 truncate max-w-[160px]">{s.reason}</span>
                  </li>
                ))}
              </ul>
              <div className="flex gap-2 pt-1">
                <button
                  onClick={handleRepairStubs}
                  disabled={repairState === "repairing"}
                  className="flex-1 px-3 py-1.5 text-[12px] font-medium bg-[var(--accent)] text-white rounded hover:opacity-90 disabled:opacity-50 transition-opacity"
                >
                  {repairState === "repairing" ? "Repairing..." : `Repair ${stubs.length} stub${stubs.length !== 1 ? "s" : ""}`}
                </button>
                <button
                  onClick={() => router.push("/")}
                  disabled={repairState === "repairing"}
                  className="px-3 py-1.5 text-[12px] text-[var(--muted)] bg-[var(--surface2)] rounded hover:bg-[var(--border)] disabled:opacity-50 transition-colors"
                >
                  Skip
                </button>
              </div>
            </div>
          )}

          {repairState === "done" && repairResults.length > 0 && (
            <div className="bg-[var(--surface)] border border-[var(--border)] rounded-lg p-4 space-y-3">
              <p className="text-[13px] font-semibold text-[var(--heading)]">
                Repair complete — {repairResults.filter((r) => r.success).length}/{repairResults.length} notes repaired
              </p>
              <ul className="space-y-1 max-h-[140px] overflow-y-auto">
                {repairResults.map((r, i) => (
                  <li key={i} className="flex items-center gap-2 text-[11px]">
                    <span className={r.success ? "text-[var(--accent)]" : "text-[var(--danger)]"}>{r.success ? "✓" : "✗"}</span>
                    <span className="text-[var(--text-secondary)] truncate flex-1">{r.title}</span>
                    {r.error && <span className="text-[var(--muted)] truncate max-w-[160px]">{r.error}</span>}
                  </li>
                ))}
              </ul>
              <button onClick={() => router.push("/")} className="w-full px-3 py-1.5 text-[12px] font-medium bg-[var(--accent)] text-white rounded hover:opacity-90 transition-opacity">
                Go to notes
              </button>
            </div>
          )}

          {liveGraph && liveGraph.nodes.length > 0 && (
            <div className="bg-[var(--surface)] border border-[var(--border)] rounded-lg overflow-hidden">
              <div className="px-3 py-2 border-b border-[var(--border)] flex items-center justify-between">
                <span className="text-[11px] font-semibold text-[var(--muted)] uppercase tracking-wider">Knowledge Graph</span>
                <span className="text-[10px] text-[var(--muted)]">{liveGraph.nodes.length} nodes · {liveGraph.edges.length} edges</span>
              </div>
              <div ref={graphContainerRef}>
                <ForceGraph data={liveGraph} onNodeClick={(slug) => router.push(`/notes/${slug}`)} width={graphWidth} height={200} mode="global" showLegend={false} />
              </div>
            </div>
          )}

          {docStatus.recentNotes.length > 0 && (
            <div className="bg-[var(--surface)] border border-[var(--border)] rounded-lg p-3">
              <h3 className="text-[11px] font-semibold text-[var(--muted)] uppercase tracking-wider mb-2">Notes Created</h3>
              <ul className="space-y-1">
                {docStatus.recentNotes.map((title, i) => (
                  <li key={i} className="text-[12px] text-[var(--text-secondary)] flex items-center gap-2 animate-[fadeIn_0.4s_ease-out]">
                    <span className="w-1.5 h-1.5 rounded-full bg-[var(--accent)] flex-shrink-0" />
                    <span className="truncate">{title}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {activityLog.length > 0 && (
            <div className="bg-[var(--surface)] border border-[var(--border)] rounded-lg overflow-hidden">
              <div className="px-3 py-2 border-b border-[var(--border)]">
                <span className="text-[11px] font-semibold text-[var(--muted)] uppercase tracking-wider">Activity Log</span>
              </div>
              <div ref={logRef} className="max-h-[160px] overflow-y-auto p-3 space-y-1">
                {activityLog.map((msg, i) => (
                  <div key={i} className="text-[11px] font-mono text-[var(--text-secondary)] leading-relaxed animate-[fadeIn_0.3s_ease-out]">
                    <span className="text-[var(--muted)] mr-2 select-none">{'>'}</span>{msg}
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

      {/* Batch mode */}
      {mode === "batch" && (
        <div className="mt-2 space-y-4">
          {/* Header */}
          <div className="bg-[var(--surface)] border border-[var(--border)] rounded-lg p-4">
            <div className="flex items-center justify-between mb-3">
              <span className="text-[14px] font-medium text-[var(--heading)]">
                Batch upload
                {batchDocs.length > 0 && ` · ${batchDocs.length} document${batchDocs.length !== 1 ? "s" : ""}`}
              </span>
              {batchStartTime && batchPhase !== "done" && (
                <span className="text-[11px] text-[var(--muted)] tabular-nums">{formatElapsed(batchElapsed)}</span>
              )}
            </div>

            {/* Upload progress (phase 1) */}
            {batchPhase === "uploading" && batchUploadProgress.length > 0 && (
              <div className="space-y-2">
                {batchUploadProgress.map((f, i) => (
                  <div key={i} className="space-y-1">
                    <div className="flex items-center justify-between">
                      <span className="text-[12px] text-[var(--text-secondary)] truncate flex-1 mr-2">{f.name}</span>
                      <span className="text-[11px] text-[var(--muted)] tabular-nums flex-shrink-0">{f.pct}%</span>
                    </div>
                    <div className="w-full h-1 bg-[var(--border)] rounded-full overflow-hidden">
                      <div className="h-full bg-[var(--accent)] rounded-full transition-all duration-300" style={{ width: `${f.pct}%` }} />
                    </div>
                  </div>
                ))}
                <p className="text-[11px] text-[var(--muted)] pt-1">
                  Uploading {batchUploadProgress.filter((f) => f.pct === 100).length}/{batchUploadProgress.length} files...
                </p>
              </div>
            )}

            {/* Per-doc processing cards (phase 2+) */}
            {batchPhase !== "uploading" && batchDocs.length > 0 && (
              <div className="space-y-2">
                {batchDocs.map((doc) => {
                  const stageIdx = stageFromStep(doc.processingStep, doc.status);
                  const pct = doc.status === "done"
                    ? 100
                    : doc.status === "error"
                    ? 0
                    : Math.round(Math.max(0, stageIdx) / (PIPELINE_STAGES.length - 1) * 100);
                  return (
                    <div key={doc.id} className="space-y-1">
                      <div className="flex items-center gap-2">
                        <span className="text-[13px]">
                          {doc.status === "done" ? "✓" : doc.status === "error" ? "✗" : "⟳"}
                        </span>
                        <span className={`text-[12px] truncate flex-1 ${
                          doc.status === "error" ? "text-[var(--danger)]" :
                          doc.status === "done" ? "text-[var(--text-secondary)]" :
                          "text-[var(--heading)]"
                        }`}>
                          {doc.originalName}
                        </span>
                        {doc.notesCount != null && doc.notesCount > 0 && (
                          <span className="text-[10px] text-[var(--accent)] flex-shrink-0">{doc.notesCount} notes</span>
                        )}
                        {doc.status === "error" && (
                          <span className="text-[10px] text-[var(--danger)] flex-shrink-0 truncate max-w-[120px]">{doc.error}</span>
                        )}
                      </div>
                      <div className="w-full h-1 bg-[var(--border)] rounded-full overflow-hidden">
                        <div
                          className={`h-full rounded-full transition-all duration-500 ${
                            doc.status === "error" ? "bg-[var(--danger)]" : "bg-[var(--accent)]"
                          }`}
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                      {doc.processingStep && doc.status === "processing" && (
                        <p className="text-[10px] text-[var(--muted)] truncate">{doc.processingStep}</p>
                      )}
                    </div>
                  );
                })}

                {/* Cross-link phase indicator */}
                {batchDocs.some((d) => d.processingStep?.toLowerCase().includes("waiting for cross") ||
                  d.processingStep?.toLowerCase().includes("detecting cross") ||
                  d.processingStep?.toLowerCase().includes("classifying") ||
                  d.processingStep?.toLowerCase().includes("updating topic clusters")) && (
                  <div className="mt-3 pt-3 border-t border-[var(--border)] flex items-center gap-2">
                    <div className="w-2 h-2 rounded-full bg-[var(--accent)] animate-pulse flex-shrink-0" />
                    <span className="text-[12px] text-[var(--accent)]">Cross-linking all documents...</span>
                  </div>
                )}
              </div>
            )}

            {batchPhase === "done" && (
              <div className="mt-2 p-3 rounded bg-[var(--accent-bg)] text-[var(--accent)] text-[13px]">
                All documents processed. Redirecting...
              </div>
            )}
          </div>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="mt-4 p-3 rounded bg-[var(--danger)]/5 border border-[var(--danger)]/20 text-[13px]">
          <p className="text-[var(--danger)]">{error}</p>
          <button onClick={resetToIdle} className="mt-2 px-3 py-1 text-[12px] bg-[var(--surface2)] text-[var(--text-secondary)] rounded hover:bg-[var(--border)] transition-colors">
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
