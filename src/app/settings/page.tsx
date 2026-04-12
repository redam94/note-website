"use client";

import { useEffect, useState } from "react";

interface SettingsData {
  simple_model: string;
  advanced_model: string;
  anthropic_api_key: string;
  lmstudio_base_url: string;
}

interface ModelsData {
  anthropic: string[];
  lmstudio: string[];
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<SettingsData>({
    simple_model: "",
    advanced_model: "",
    anthropic_api_key: "",
    lmstudio_base_url: "",
  });
  const [models, setModels] = useState<ModelsData>({ anthropic: [], lmstudio: [] });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testResult, setTestResult] = useState<{
    provider: string;
    success: boolean;
    message: string;
  } | null>(null);

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
    setTestResult(null);
    const res = await fetch("/api/settings/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider }),
    });
    setTestResult({ provider, ...(await res.json()) });
  };

  if (loading) return <div className="p-8 text-[var(--muted)]">Loading settings...</div>;

  const selectClass =
    "w-full px-3 py-2 text-[14px] bg-[var(--bg)] border border-[var(--border)] rounded text-[var(--text)] focus:outline-none focus:border-[var(--accent-light)]";
  const inputClass = selectClass;
  const labelClass = "block text-[13px] font-medium text-[var(--text-secondary)] mb-1";
  const sectionClass = "bg-[var(--surface)] rounded-lg p-5 border border-[var(--border)]";
  const btnSecondary =
    "px-3 py-1.5 text-[13px] bg-[var(--surface2)] text-[var(--text-secondary)] rounded border border-[var(--border)] hover:bg-[var(--border)] transition-colors";

  return (
    <div className="max-w-[620px] mx-auto px-8 py-8">
      <h1 className="text-[24px] font-bold text-[var(--heading)] mb-6">Settings</h1>

      <div className="space-y-5">
        <section className={sectionClass}>
          <h2 className="text-[16px] font-semibold text-[var(--heading)] mb-4">
            Model Configuration
          </h2>
          <div className="space-y-4">
            <div>
              <label className={labelClass}>Simple Model</label>
              <select
                value={settings.simple_model}
                onChange={(e) => setSettings({ ...settings, simple_model: e.target.value })}
                className={selectClass}
              >
                <optgroup label="Anthropic">
                  {models.anthropic.map((m) => <option key={m} value={m}>{m}</option>)}
                </optgroup>
                {models.lmstudio.length > 0 && (
                  <optgroup label="LMStudio">
                    {models.lmstudio.map((m) => <option key={m} value={m}>{m}</option>)}
                  </optgroup>
                )}
              </select>
            </div>
            <div>
              <label className={labelClass}>Advanced Model</label>
              <select
                value={settings.advanced_model}
                onChange={(e) => setSettings({ ...settings, advanced_model: e.target.value })}
                className={selectClass}
              >
                <optgroup label="Anthropic">
                  {models.anthropic.map((m) => <option key={m} value={m}>{m}</option>)}
                </optgroup>
                {models.lmstudio.length > 0 && (
                  <optgroup label="LMStudio">
                    {models.lmstudio.map((m) => <option key={m} value={m}>{m}</option>)}
                  </optgroup>
                )}
              </select>
            </div>
          </div>
        </section>

        <section className={sectionClass}>
          <h2 className="text-[16px] font-semibold text-[var(--heading)] mb-4">
            Anthropic API
          </h2>
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
            <button onClick={() => handleTest("anthropic")} className={btnSecondary}>
              Test Connection
            </button>
            {testResult?.provider === "anthropic" && (
              <p className={`text-[13px] ${testResult.success ? "text-[var(--callout-green)]" : "text-[var(--danger)]"}`}>
                {testResult.message}
              </p>
            )}
          </div>
        </section>

        <section className={sectionClass}>
          <h2 className="text-[16px] font-semibold text-[var(--heading)] mb-4">
            LMStudio
          </h2>
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
            <button onClick={() => handleTest("lmstudio")} className={btnSecondary}>
              Test Connection
            </button>
            {testResult?.provider === "lmstudio" && (
              <p className={`text-[13px] ${testResult.success ? "text-[var(--callout-green)]" : "text-[var(--danger)]"}`}>
                {testResult.message}
              </p>
            )}
          </div>
        </section>

        <button
          onClick={handleSave}
          disabled={saving}
          className="w-full py-2.5 bg-[var(--accent)] hover:bg-[var(--link)] disabled:opacity-50 text-white text-[14px] font-medium rounded-lg transition-colors"
        >
          {saving ? "Saving..." : "Save Settings"}
        </button>
      </div>
    </div>
  );
}
