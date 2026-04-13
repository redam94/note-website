"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";

interface DocStatus {
  id: number;
  originalName: string;
  status: string;
  error: string | null;
  processingStep: string | null;
  notesCount: number | null;
}

const PIPELINE_STAGES = [
  { key: "upload", label: "Upload" },
  { key: "parse", label: "Parse document" },
  { key: "extract", label: "Extract structure" },
  { key: "outline", label: "Build outline" },
  { key: "plan", label: "Plan notes" },
  { key: "create", label: "Create notes" },
  { key: "index", label: "Generate indexes" },
  { key: "links", label: "Insert links" },
  { key: "crosslink", label: "Cross-link" },
  { key: "done", label: "Complete" },
];

function stageFromStep(step: string | null, status: string): number {
  if (status === "done") return PIPELINE_STAGES.length - 1;
  if (status === "error") return -1;
  if (!step) return 0;
  const s = step.toLowerCase();
  if (s.includes("parsing") || s.includes("parsed")) return 1;
  if (s.includes("extracting") || s.includes("structure extracted")) return 2;
  if (s.includes("using extracted") || s.includes("outline")) return 3;
  if (s.includes("planning") || s.includes("planned")) return 4;
  if (s.includes("creating note")) return 5;
  if (s.includes("generating index")) return 6;
  if (s.includes("inserting wiki") || s.includes("inserting link")) return 7;
  if (s.includes("cross-link") || s.includes("detecting cross") || s.includes("classifying")) return 8;
  return 1; // default to parse if processing
}

export default function UploadForm() {
  const [isDragging, setIsDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [docStatus, setDocStatus] = useState<DocStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [completedSteps, setCompletedSteps] = useState<string[]>([]);
  const pollRef = useRef<NodeJS.Timeout | null>(null);
  const router = useRouter();

  // On mount: check for in-progress documents and resume polling
  useEffect(() => {
    async function checkInProgress() {
      try {
        const res = await fetch("/api/documents");
        if (!res.ok) return;
        const docs = await res.json();
        const processing = docs.find(
          (d: any) => d.status === "processing" || d.status === "pending"
        );
        if (processing) {
          setUploading(true);
          setDocStatus({
            id: processing.id,
            originalName: processing.originalName,
            status: processing.status,
            error: null,
            processingStep: processing.processingStep || "Resuming...",
            notesCount: processing.notesCount,
          });
          startPolling(processing.id);
        }
      } catch { /* ignore */ }
    }
    checkInProgress();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  function startPolling(docId: number) {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const statusRes = await fetch("/api/documents");
        if (!statusRes.ok) return;
        const docs = await statusRes.json();
        const current = docs.find((d: any) => d.id === docId);
        if (!current) return;

        setDocStatus({
          id: current.id,
          originalName: current.originalName,
          status: current.status,
          error: current.error,
          processingStep: current.processingStep,
          notesCount: current.notesCount,
        });

        if (current.status === "done") {
          if (pollRef.current) clearInterval(pollRef.current);
          setUploading(false);
          window.dispatchEvent(new Event("sidebar-refresh"));
          setTimeout(() => router.push("/"), 2500);
        } else if (current.status === "error") {
          if (pollRef.current) clearInterval(pollRef.current);
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
      setCompletedSteps(["upload"]);

      try {
        const formData = new FormData();
        formData.append("file", file);

        const res = await fetch("/api/documents", {
          method: "POST",
          body: formData,
        });

        if (!res.ok) throw new Error(await res.text());

        const doc = await res.json();
        setDocStatus({
          id: doc.id,
          originalName: doc.originalName,
          status: "processing",
          error: null,
          processingStep: "Starting pipeline...",
          notesCount: null,
        });

        startPolling(doc.id);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Upload failed");
        setUploading(false);
      }
    },
    [router]
  );

  const currentStageIdx = docStatus
    ? stageFromStep(docStatus.processingStep, docStatus.status)
    : uploading
      ? 0
      : -1;

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
        <div className="mt-2 bg-[var(--surface)] border border-[var(--border)] rounded-lg p-5">
          {/* File name */}
          <div className="flex items-center gap-2 mb-4">
            <span className="text-[15px]">📄</span>
            <span className="text-[14px] font-medium text-[var(--heading)]">
              {docStatus.originalName}
            </span>
            {docStatus.notesCount != null && docStatus.notesCount > 0 && (
              <span className="text-[12px] text-[var(--muted)] ml-auto">
                {docStatus.notesCount} notes
              </span>
            )}
          </div>

          {/* Pipeline stages */}
          <div className="space-y-0">
            {PIPELINE_STAGES.map((stage, idx) => {
              const isActive = idx === currentStageIdx;
              const isCompleted = currentStageIdx > idx || docStatus.status === "done";
              const isFuture = idx > currentStageIdx && docStatus.status !== "done";

              return (
                <div key={stage.key} className="flex items-start gap-3 relative">
                  {/* Vertical line */}
                  {idx < PIPELINE_STAGES.length - 1 && (
                    <div
                      className="absolute left-[9px] top-[20px] w-[2px] h-[20px]"
                      style={{
                        backgroundColor: isCompleted ? "var(--accent)" : "var(--border)",
                      }}
                    />
                  )}

                  {/* Circle indicator */}
                  <div className="flex-shrink-0 mt-[2px]">
                    {isCompleted ? (
                      <div className="w-5 h-5 rounded-full bg-[var(--accent)] flex items-center justify-center">
                        <svg className="w-3 h-3 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                        </svg>
                      </div>
                    ) : isActive ? (
                      <div className="w-5 h-5 rounded-full border-2 border-[var(--accent)] flex items-center justify-center">
                        <div className="w-2 h-2 rounded-full bg-[var(--accent)] animate-pulse" />
                      </div>
                    ) : (
                      <div className="w-5 h-5 rounded-full border-2 border-[var(--border)]" />
                    )}
                  </div>

                  {/* Label + detail */}
                  <div className="pb-4 flex-1 min-w-0">
                    <p className={`text-[13px] leading-tight ${
                      isActive ? "text-[var(--heading)] font-medium" :
                      isCompleted ? "text-[var(--text-secondary)]" :
                      "text-[var(--muted)]"
                    }`}>
                      {stage.label}
                    </p>
                    {isActive && docStatus.processingStep && (
                      <p className="text-[12px] text-[var(--accent)] mt-0.5 truncate">
                        {docStatus.processingStep}
                      </p>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Done message */}
          {docStatus.status === "done" && (
            <div className="mt-2 p-3 rounded bg-[var(--accent-bg)] text-[var(--accent)] text-[13px]">
              Processing complete. Redirecting...
            </div>
          )}
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="mt-4 p-3 rounded bg-[var(--danger)]/5 border border-[var(--danger)]/20 text-[13px]">
          <p className="text-[var(--danger)]">{error}</p>
          <button
            onClick={() => { setError(null); setDocStatus(null); setUploading(false); }}
            className="mt-2 px-3 py-1 text-[12px] bg-[var(--surface2)] text-[var(--text-secondary)] rounded hover:bg-[var(--border)] transition-colors"
          >
            Try again
          </button>
        </div>
      )}
    </div>
  );
}
