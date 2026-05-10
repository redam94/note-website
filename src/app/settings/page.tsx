"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

interface SettingsData {
  anthropic_api_key: string;
  openai_api_key: string;
  google_api_key: string;
  lmstudio_base_url: string;
  model_outline: string;
  model_plan: string;
  model_create: string;
  model_links: string;
  model_crosslink: string;
  model_index: string;
  model_community: string;
  model_ask: string;
}

interface ModelsData {
  anthropic: string[];
  openai: string[];
  google: string[];
  lmstudio: string[];
}

const TASK_MODELS: { key: keyof SettingsData; label: string }[] = [
  { key: "model_outline",   label: "Outline detection" },
  { key: "model_plan",      label: "Planning" },
  { key: "model_create",    label: "Note creation" },
  { key: "model_links",     label: "Link insertion" },
  { key: "model_crosslink", label: "Cross-linking" },
  { key: "model_index",     label: "Index generation" },
  { key: "model_community", label: "Community detection" },
  { key: "model_ask",       label: "Q&A" },
];

const EMPTY_SETTINGS: SettingsData = {
  anthropic_api_key: "",
  openai_api_key: "",
  google_api_key: "",
  lmstudio_base_url: "",
  model_outline: "",
  model_plan: "",
  model_create: "",
  model_links: "",
  model_crosslink: "",
  model_index: "",
  model_community: "",
  model_ask: "",
};

export default function SettingsPage() {
  const [settings, setSettings] = useState<SettingsData>(EMPTY_SETTINGS);
  const [models, setModels] = useState<ModelsData>({ anthropic: [], openai: [], google: [], lmstudio: [] });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testResults, setTestResults] = useState<Record<string, { success: boolean; message: string }>>({});

  useEffect(() => {
    Promise.all([
      fetch("/api/settings").then((r) => r.json()),
      fetch("/api/settings/models").then((r) => r.json()),
    ]).then(([s, m]) => {
      setSettings(s);
      setModels(m);
      setLoading(false);
    });
  }, []);

  const handleSave = async () => {
    setSaving(true);
    try {
      const res = await fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settings),
      });
      setSettings(await res.json());
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async (provider: string) => {
    setTestResults((prev) => ({ ...prev, [provider]: { success: false, message: "Testing..." } }));
    const res = await fetch("/api/settings/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider }),
    });
    const data = await res.json();
    setTestResults((prev) => ({ ...prev, [provider]: data }));
  };

  if (loading) return <div className="p-8 text-[var(--muted)]">Loading settings...</div>;

  const selectClass =
    "w-full px-3 py-2 text-[14px] bg-[var(--bg)] border border-[var(--border)] rounded text-[var(--text)] focus:outline-none focus:border-[var(--accent-light)]";
  const inputClass = selectClass;
  const labelClass = "block text-[13px] font-medium text-[var(--text-secondary)] mb-1";
  const sectionClass = "bg-[var(--surface)] rounded-lg p-5 border border-[var(--border)]";
  const btnSecondary =
    "px-3 py-1.5 text-[13px] bg-[var(--surface2)] text-[var(--text-secondary)] rounded border border-[var(--border)] hover:bg-[var(--border)] transition-colors";

  function ModelSelect({ field }: { field: keyof SettingsData }) {
    return (
      <select
        value={settings[field]}
        onChange={(e) => setSettings({ ...settings, [field]: e.target.value })}
        className={selectClass}
      >
        {models.anthropic.length > 0 && (
          <optgroup label="Anthropic">
            {models.anthropic.map((m) => <option key={m} value={m}>{m}</option>)}
          </optgroup>
        )}
        {models.openai.length > 0 && (
          <optgroup label="OpenAI">
            {models.openai.map((m) => <option key={m} value={m}>{m}</option>)}
          </optgroup>
        )}
        {models.google.length > 0 && (
          <optgroup label="Google">
            {models.google.map((m) => <option key={m} value={m}>{m}</option>)}
          </optgroup>
        )}
        {models.lmstudio.length > 0 && (
          <optgroup label="Custom / Local">
            {models.lmstudio.map((m) => <option key={m} value={m}>{m}</option>)}
          </optgroup>
        )}
      </select>
    );
  }

  function TestStatus({ provider }: { provider: string }) {
    const result = testResults[provider];
    if (!result) return null;
    return (
      <p className={`text-[13px] ${result.success ? "text-[var(--callout-green)]" : "text-[var(--danger)]"}`}>
        {result.message}
      </p>
    );
  }

  return (
    <div className="max-w-[620px] mx-auto px-4 py-6 md:px-8 md:py-8">
      <h1 className="text-[24px] font-bold text-[var(--heading)] mb-6">Settings</h1>

      <div className="space-y-5">

        {/* Task Models */}
        <section className={sectionClass}>
          <h2 className="text-[16px] font-semibold text-[var(--heading)] mb-4">Task Models</h2>
          <div className="space-y-3">
            {TASK_MODELS.map(({ key, label }) => (
              <div key={key}>
                <label className={labelClass}>{label}</label>
                <ModelSelect field={key} />
              </div>
            ))}
          </div>
        </section>

        {/* Anthropic */}
        <section className={sectionClass}>
          <h2 className="text-[16px] font-semibold text-[var(--heading)] mb-4">Anthropic</h2>
          <div className="space-y-3">
            <div>
              <label className={labelClass}>API Key</label>
              <input
                type="password"
                value={settings.anthropic_api_key}
                onChange={(e) => setSettings({ ...settings, anthropic_api_key: e.target.value })}
                placeholder="sk-ant-..."
                className={inputClass}
              />
            </div>
            <button onClick={() => handleTest("anthropic")} className={btnSecondary}>Test Connection</button>
            <TestStatus provider="anthropic" />
          </div>
        </section>

        {/* OpenAI */}
        <section className={sectionClass}>
          <h2 className="text-[16px] font-semibold text-[var(--heading)] mb-4">OpenAI</h2>
          <div className="space-y-3">
            <div>
              <label className={labelClass}>API Key</label>
              <input
                type="password"
                value={settings.openai_api_key}
                onChange={(e) => setSettings({ ...settings, openai_api_key: e.target.value })}
                placeholder="sk-..."
                className={inputClass}
              />
            </div>
            <button onClick={() => handleTest("openai")} className={btnSecondary}>Test Connection</button>
            <TestStatus provider="openai" />
          </div>
        </section>

        {/* Google */}
        <section className={sectionClass}>
          <h2 className="text-[16px] font-semibold text-[var(--heading)] mb-4">Google AI</h2>
          <div className="space-y-3">
            <div>
              <label className={labelClass}>API Key</label>
              <input
                type="password"
                value={settings.google_api_key}
                onChange={(e) => setSettings({ ...settings, google_api_key: e.target.value })}
                placeholder="AIza..."
                className={inputClass}
              />
            </div>
            <button onClick={() => handleTest("google")} className={btnSecondary}>Test Connection</button>
            <TestStatus provider="google" />
          </div>
        </section>

        {/* Custom / Local */}
        <section className={sectionClass}>
          <h2 className="text-[16px] font-semibold text-[var(--heading)] mb-4">Custom / Local</h2>
          <div className="space-y-3">
            <div>
              <label className={labelClass}>Base URL</label>
              <input
                type="text"
                value={settings.lmstudio_base_url}
                onChange={(e) => setSettings({ ...settings, lmstudio_base_url: e.target.value })}
                placeholder="http://localhost:1234/v1"
                className={inputClass}
              />
            </div>
            <button onClick={() => handleTest("lmstudio")} className={btnSecondary}>Test Connection</button>
            <TestStatus provider="lmstudio" />
          </div>
        </section>

        <button
          onClick={handleSave}
          disabled={saving}
          className="w-full py-2.5 bg-[var(--accent)] hover:bg-[var(--link)] disabled:opacity-50 text-white text-[14px] font-medium rounded-lg transition-colors"
        >
          {saving ? "Saving..." : "Save Settings"}
        </button>

        {/* Extraction profiles */}
        <section className="bg-[var(--surface)] border border-[var(--border)] rounded-xl p-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="text-[16px] font-semibold text-[var(--heading)] mb-1">Extraction Profiles</h2>
              <p className="text-[13px] text-[var(--text-secondary)]">
                Map custom file extensions (e.g. <code className="font-mono text-[11px]">.eml</code>,{" "}
                <code className="font-mono text-[11px]">.zip</code>) to Python extraction scripts that
                transform raw files before the pipeline runs.
              </p>
            </div>
            <Link
              href="/settings/extraction"
              className="flex-shrink-0 px-3 py-1.5 text-[12px] font-medium border border-[var(--border)] rounded-lg text-[var(--text-secondary)] hover:border-[var(--accent-light)] hover:text-[var(--accent)] transition-all"
            >
              Manage →
            </Link>
          </div>
        </section>

        {/* Integrations */}
        <section className="bg-[var(--surface)] border border-[var(--border)] rounded-xl p-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="text-[16px] font-semibold text-[var(--heading)] mb-1">Integrations</h2>
              <p className="text-[13px] text-[var(--text-secondary)]">
                Link GitHub accounts once. Connections live globally; each vault
                picks which repos to ingest separately.
              </p>
            </div>
            <Link
              href="/settings/integrations"
              className="flex-shrink-0 px-3 py-1.5 text-[12px] font-medium border border-[var(--border)] rounded-lg text-[var(--text-secondary)] hover:border-[var(--accent-light)] hover:text-[var(--accent)] transition-all"
            >
              Manage →
            </Link>
          </div>
        </section>

        {/* Vault repos */}
        <section className="bg-[var(--surface)] border border-[var(--border)] rounded-xl p-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="text-[16px] font-semibold text-[var(--heading)] mb-1">Vault repos</h2>
              <p className="text-[13px] text-[var(--text-secondary)]">
                Per-vault: track GitHub repos that stream issues, PRs, wiki
                pages, and source files into the current vault as notes.
              </p>
            </div>
            <Link
              href="/settings/repos"
              className="flex-shrink-0 px-3 py-1.5 text-[12px] font-medium border border-[var(--border)] rounded-lg text-[var(--text-secondary)] hover:border-[var(--accent-light)] hover:text-[var(--accent)] transition-all"
            >
              Manage →
            </Link>
          </div>
        </section>

        {/* Publish */}
        <section className="bg-[var(--surface)] border border-[var(--border)] rounded-xl p-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="text-[16px] font-semibold text-[var(--heading)] mb-1">Publish</h2>
              <p className="text-[13px] text-[var(--text-secondary)]">
                Push a space&apos;s public notes to a GitHub repo&apos;s wiki. Admin-only notes are never
                included.
              </p>
            </div>
            <Link
              href="/settings/publish"
              className="flex-shrink-0 px-3 py-1.5 text-[12px] font-medium border border-[var(--border)] rounded-lg text-[var(--text-secondary)] hover:border-[var(--accent-light)] hover:text-[var(--accent)] transition-all"
            >
              Manage →
            </Link>
          </div>
        </section>

      </div>
    </div>
  );
}
