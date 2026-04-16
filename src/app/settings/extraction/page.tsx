"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useSpace } from "@/contexts/SpaceContext";
import { apiUrl } from "@/lib/api";

interface Profile {
  id: number;
  name: string;
  description: string;
  extensions: string[];
  mime_types: string[];
  doc_type_override: string | null;
  script: string;
  prompt_additions: string;
  created_at: string;
}

const EMPTY_PROFILE: Omit<Profile, "id" | "created_at"> = {
  name: "",
  description: "",
  extensions: [],
  mime_types: [],
  doc_type_override: null,
  script: "",
  prompt_additions: "",
};

const DOC_TYPE_OPTIONS = [
  { value: "", label: "Auto-detect" },
  { value: "textbook", label: "Textbook" },
  { value: "paper", label: "Research paper" },
  { value: "tutorial", label: "Tutorial" },
  { value: "reference", label: "Reference / API docs" },
  { value: "article", label: "Article / blog post" },
  { value: "report", label: "Report" },
  { value: "email", label: "Email / thread" },
  { value: "codebase", label: "Codebase" },
];

const SCRIPT_PLACEHOLDER = `# Extraction script example
# sys.argv[1] is the path to the uploaded file.
# Print the extracted text to stdout.
import sys

with open(sys.argv[1], "r", encoding="utf-8", errors="replace") as f:
    text = f.read()

# Transform the text here...
print(text)
`;

export default function ExtractionProfilesPage() {
  const { spaceSlug } = useSpace();
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<Profile | null>(null);
  const [form, setForm] = useState(EMPTY_PROFILE);
  const [extInput, setExtInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);

  // Script test state
  const [testSample, setTestSample] = useState("");
  const [testResult, setTestResult] = useState<{
    stdout: string; stderr: string; exit_code: number; success: boolean;
  } | null>(null);
  const [testing, setTesting] = useState(false);

  async function fetchProfiles() {
    try {
      const res = await fetch(apiUrl("/api/extraction-profiles", spaceSlug));
      if (res.ok) setProfiles(await res.json());
    } catch { /* ignore */ }
    setLoading(false);
  }

  useEffect(() => { fetchProfiles(); }, [spaceSlug]);

  function startCreate() {
    setEditing(null);
    setForm(EMPTY_PROFILE);
    setExtInput("");
    setSaveError(null);
    setTestResult(null);
  }

  function startEdit(p: Profile) {
    setEditing(p);
    setForm({
      name: p.name,
      description: p.description,
      extensions: p.extensions,
      mime_types: p.mime_types,
      doc_type_override: p.doc_type_override,
      script: p.script,
      prompt_additions: p.prompt_additions,
    });
    setExtInput("");
    setSaveError(null);
    setTestResult(null);
  }

  function cancelEdit() {
    setEditing(null);
    setForm(EMPTY_PROFILE);
    setSaveError(null);
    setTestResult(null);
  }

  function addExtension() {
    const ext = extInput.trim().replace(/^\./, "").toLowerCase();
    if (ext && !form.extensions.includes(ext)) {
      setForm((f) => ({ ...f, extensions: [...f.extensions, ext] }));
    }
    setExtInput("");
  }

  function removeExtension(ext: string) {
    setForm((f) => ({ ...f, extensions: f.extensions.filter((e) => e !== ext) }));
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (!form.name.trim()) { setSaveError("Name is required"); return; }
    setSaving(true);
    setSaveError(null);
    try {
      const body = {
        ...form,
        doc_type_override: form.doc_type_override || null,
      };
      const url = editing
        ? apiUrl(`/api/extraction-profiles/${editing.id}`, spaceSlug)
        : apiUrl("/api/extraction-profiles", spaceSlug);
      const method = editing ? "PATCH" : "POST";
      const res = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const text = await res.text();
        setSaveError(text || `HTTP ${res.status}`);
        return;
      }
      await fetchProfiles();
      cancelEdit();
    } catch (err) {
      setSaveError(String(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: number) {
    setDeleting(id);
    try {
      await fetch(apiUrl(`/api/extraction-profiles/${id}`, spaceSlug), { method: "DELETE" });
      await fetchProfiles();
    } finally {
      setDeleting(null);
    }
  }

  async function handleTest() {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await fetch(apiUrl("/api/extraction-profiles/test", spaceSlug), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ script: form.script, sample_text: testSample }),
      });
      if (res.ok) setTestResult(await res.json());
    } catch { /* ignore */ }
    setTesting(false);
  }

  const isEditorOpen = editing !== null || form.name !== "" || form.script !== "" || form.extensions.length > 0;

  return (
    <div className="max-w-3xl mx-auto px-4 py-8">
      <div className="flex items-center gap-2 mb-1">
        <Link
          href="/settings"
          className="text-[12px] text-[var(--muted)] hover:text-[var(--text-secondary)] transition-colors"
        >
          Settings
        </Link>
        <span className="text-[var(--muted)]">/</span>
        <span className="text-[12px] text-[var(--text-secondary)]">Extraction Profiles</span>
      </div>
      <h1 className="text-[22px] font-bold text-[var(--heading)] mb-2">Extraction Profiles</h1>
      <p className="text-[13px] text-[var(--text-secondary)] mb-6 leading-relaxed">
        Define named profiles that map file extensions to custom Python scripts.
        When you upload a matching file, the script transforms its raw text before
        the note-creation pipeline runs — enabling support for emails, codebases,
        reports, and any other format.
      </p>

      {/* Profile list */}
      {loading ? (
        <p className="text-[13px] text-[var(--muted)]">Loading...</p>
      ) : profiles.length === 0 && !isEditorOpen ? (
        <div className="border border-dashed border-[var(--border)] rounded-lg p-8 text-center">
          <p className="text-[14px] text-[var(--muted)] mb-3">No extraction profiles yet</p>
          <button
            onClick={startCreate}
            className="px-4 py-2 text-[13px] font-medium bg-[var(--accent)] text-white rounded-lg hover:opacity-90 transition-opacity"
          >
            Create first profile
          </button>
        </div>
      ) : (
        <div className="space-y-2 mb-6">
          {profiles.map((p) => (
            <div
              key={p.id}
              className="flex items-start gap-3 p-4 bg-[var(--surface)] border border-[var(--border)] rounded-lg"
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-[14px] font-semibold text-[var(--heading)]">{p.name}</span>
                  {p.extensions.map((ext) => (
                    <span
                      key={ext}
                      className="px-1.5 py-0.5 text-[10px] font-mono bg-[var(--surface2)] text-[var(--text-secondary)] rounded border border-[var(--border)]"
                    >
                      .{ext}
                    </span>
                  ))}
                  {p.doc_type_override && (
                    <span className="px-1.5 py-0.5 text-[10px] bg-[var(--accent-bg)] text-[var(--accent)] rounded">
                      {p.doc_type_override}
                    </span>
                  )}
                </div>
                {p.description && (
                  <p className="text-[12px] text-[var(--muted)] mt-0.5 truncate">{p.description}</p>
                )}
              </div>
              <div className="flex gap-2 flex-shrink-0">
                <button
                  onClick={() => startEdit(p)}
                  className="px-2.5 py-1 text-[11px] text-[var(--text-secondary)] border border-[var(--border)] rounded hover:border-[var(--accent-light)] hover:text-[var(--accent)] transition-all"
                >
                  Edit
                </button>
                <button
                  onClick={() => handleDelete(p.id)}
                  disabled={deleting === p.id}
                  className="px-2.5 py-1 text-[11px] text-[var(--danger)] border border-[var(--danger)]/30 rounded hover:bg-[var(--danger)]/5 disabled:opacity-40 transition-all"
                >
                  {deleting === p.id ? "..." : "Delete"}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Create button (when no editor open and profiles exist) */}
      {!isEditorOpen && profiles.length > 0 && (
        <button
          onClick={startCreate}
          className="mb-6 flex items-center gap-1.5 px-3 py-1.5 text-[12px] font-medium border border-[var(--border)] rounded-lg text-[var(--text-secondary)] hover:border-[var(--accent-light)] hover:text-[var(--accent)] transition-all"
        >
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
          </svg>
          New profile
        </button>
      )}

      {/* Editor form */}
      {isEditorOpen && (
        <form onSubmit={handleSave} className="bg-[var(--surface)] border border-[var(--border)] rounded-lg p-5 space-y-5">
          <h2 className="text-[15px] font-semibold text-[var(--heading)]">
            {editing ? `Edit: ${editing.name}` : "New Extraction Profile"}
          </h2>

          {/* Name + description */}
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label className="block text-[11px] font-semibold text-[var(--muted)] uppercase tracking-wider mb-1">
                Profile name *
              </label>
              <input
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="Email threads"
                className="w-full px-3 py-2 text-[13px] bg-[var(--bg)] border border-[var(--border)] rounded-md text-[var(--text)] placeholder-[var(--muted)] focus:outline-none focus:border-[var(--accent)] focus:ring-1 focus:ring-[var(--accent)]/30"
              />
            </div>
            <div>
              <label className="block text-[11px] font-semibold text-[var(--muted)] uppercase tracking-wider mb-1">
                Description
              </label>
              <input
                value={form.description}
                onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                placeholder="Handles .eml email files"
                className="w-full px-3 py-2 text-[13px] bg-[var(--bg)] border border-[var(--border)] rounded-md text-[var(--text)] placeholder-[var(--muted)] focus:outline-none focus:border-[var(--accent)] focus:ring-1 focus:ring-[var(--accent)]/30"
              />
            </div>
          </div>

          {/* Extensions */}
          <div>
            <label className="block text-[11px] font-semibold text-[var(--muted)] uppercase tracking-wider mb-1">
              File extensions
            </label>
            <div className="flex gap-2">
              <input
                value={extInput}
                onChange={(e) => setExtInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") { e.preventDefault(); addExtension(); }
                }}
                placeholder="eml"
                className="flex-1 px-3 py-2 text-[13px] font-mono bg-[var(--bg)] border border-[var(--border)] rounded-md text-[var(--text)] placeholder-[var(--muted)] focus:outline-none focus:border-[var(--accent)] focus:ring-1 focus:ring-[var(--accent)]/30"
              />
              <button
                type="button"
                onClick={addExtension}
                className="px-3 py-2 text-[12px] bg-[var(--surface2)] border border-[var(--border)] rounded-md text-[var(--text-secondary)] hover:border-[var(--accent-light)] transition-all"
              >
                Add
              </button>
            </div>
            {form.extensions.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mt-2">
                {form.extensions.map((ext) => (
                  <span
                    key={ext}
                    className="flex items-center gap-1 px-2 py-0.5 text-[11px] font-mono bg-[var(--surface2)] text-[var(--text-secondary)] rounded border border-[var(--border)]"
                  >
                    .{ext}
                    <button
                      type="button"
                      onClick={() => removeExtension(ext)}
                      className="text-[var(--muted)] hover:text-[var(--danger)] transition-colors"
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* Doc type override */}
          <div>
            <label className="block text-[11px] font-semibold text-[var(--muted)] uppercase tracking-wider mb-1">
              Document type override
            </label>
            <select
              value={form.doc_type_override ?? ""}
              onChange={(e) => setForm((f) => ({ ...f, doc_type_override: e.target.value || null }))}
              className="w-full px-3 py-2 text-[13px] bg-[var(--bg)] border border-[var(--border)] rounded-md text-[var(--text)] focus:outline-none focus:border-[var(--accent)] focus:ring-1 focus:ring-[var(--accent)]/30"
            >
              {DOC_TYPE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
            <p className="text-[11px] text-[var(--muted)] mt-1">
              Skip the classifier and treat every matching file as this type.
            </p>
          </div>

          {/* Prompt additions */}
          <div>
            <label className="block text-[11px] font-semibold text-[var(--muted)] uppercase tracking-wider mb-1">
              LLM prompt additions
            </label>
            <textarea
              value={form.prompt_additions}
              onChange={(e) => setForm((f) => ({ ...f, prompt_additions: e.target.value }))}
              rows={3}
              placeholder="Extra instructions appended to the note-creation system prompt for files matching this profile."
              className="w-full px-3 py-2 text-[13px] bg-[var(--bg)] border border-[var(--border)] rounded-md text-[var(--text)] placeholder-[var(--muted)] focus:outline-none focus:border-[var(--accent)] focus:ring-1 focus:ring-[var(--accent)]/30 resize-y font-mono"
            />
          </div>

          {/* Extraction script */}
          <div>
            <label className="block text-[11px] font-semibold text-[var(--muted)] uppercase tracking-wider mb-1">
              Extraction script (Python)
            </label>
            <p className="text-[11px] text-[var(--muted)] mb-2">
              The script runs in a subprocess. <code className="font-mono">sys.argv[1]</code> is the file path.
              Print the extracted text to <code className="font-mono">stdout</code>. Falls back to the standard
              parser on error.
            </p>
            <textarea
              value={form.script}
              onChange={(e) => setForm((f) => ({ ...f, script: e.target.value }))}
              rows={12}
              placeholder={SCRIPT_PLACEHOLDER}
              spellCheck={false}
              className="w-full px-3 py-2 text-[12px] font-mono bg-[var(--bg)] border border-[var(--border)] rounded-md text-[var(--text)] placeholder-[var(--muted)] focus:outline-none focus:border-[var(--accent)] focus:ring-1 focus:ring-[var(--accent)]/30 resize-y"
            />
          </div>

          {/* Script test panel */}
          {form.script.trim() && (
            <div className="border border-[var(--border)] rounded-lg p-4 space-y-3">
              <p className="text-[12px] font-semibold text-[var(--text-secondary)]">Test script</p>
              <textarea
                value={testSample}
                onChange={(e) => setTestSample(e.target.value)}
                rows={4}
                placeholder="Paste sample text here. It will be written to a temp file and passed to your script as sys.argv[1]."
                className="w-full px-3 py-2 text-[12px] bg-[var(--bg)] border border-[var(--border)] rounded-md text-[var(--text)] placeholder-[var(--muted)] focus:outline-none focus:border-[var(--accent)] focus:ring-1 focus:ring-[var(--accent)]/30 resize-y"
              />
              <button
                type="button"
                onClick={handleTest}
                disabled={testing || !testSample.trim()}
                className="px-3 py-1.5 text-[12px] font-medium bg-[var(--surface2)] border border-[var(--border)] rounded-md text-[var(--text-secondary)] hover:border-[var(--accent-light)] hover:text-[var(--accent)] disabled:opacity-40 transition-all"
              >
                {testing ? "Running..." : "Run test"}
              </button>
              {testResult && (
                <div className="space-y-2">
                  <div className={`px-2 py-1 rounded text-[11px] font-medium ${testResult.success ? "bg-green-500/10 text-green-400" : "bg-[var(--danger)]/10 text-[var(--danger)]"}`}>
                    Exit code: {testResult.exit_code} — {testResult.success ? "success" : "error"}
                  </div>
                  {testResult.stdout && (
                    <pre className="px-3 py-2 text-[11px] font-mono bg-[var(--bg)] border border-[var(--border)] rounded max-h-[200px] overflow-y-auto whitespace-pre-wrap text-[var(--text-secondary)]">
                      {testResult.stdout}
                    </pre>
                  )}
                  {testResult.stderr && (
                    <pre className="px-3 py-2 text-[11px] font-mono bg-[var(--danger)]/5 border border-[var(--danger)]/20 rounded max-h-[120px] overflow-y-auto whitespace-pre-wrap text-[var(--danger)]">
                      {testResult.stderr}
                    </pre>
                  )}
                </div>
              )}
            </div>
          )}

          {saveError && (
            <p className="text-[12px] text-[var(--danger)] bg-[var(--danger)]/5 border border-[var(--danger)]/20 px-3 py-2 rounded">
              {saveError}
            </p>
          )}

          <div className="flex gap-2 pt-1">
            <button
              type="submit"
              disabled={saving}
              className="px-4 py-2 text-[13px] font-medium bg-[var(--accent)] text-white rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity"
            >
              {saving ? "Saving..." : editing ? "Save changes" : "Create profile"}
            </button>
            <button
              type="button"
              onClick={cancelEdit}
              className="px-4 py-2 text-[13px] text-[var(--text-secondary)] bg-[var(--surface2)] rounded-lg hover:bg-[var(--border)] transition-colors"
            >
              Cancel
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
