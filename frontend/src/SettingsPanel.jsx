import { useEffect, useState } from "react";
import { KeyRound, CheckCircle2, AlertTriangle, Trash2 } from "lucide-react";

import {
  clearApplications,
  getSettings,
  requestMessage,
  saveSettings,
  testApiKey,
} from "./api";

export default function SettingsPanel({ onChanged }) {
  const [settings, setSettings] = useState(null);
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    getSettings().then(setSettings).catch((e) => setError(requestMessage(e)));
  }, []);

  function change(key, value) {
    setSettings((s) => ({ ...s, [key]: value }));
  }

  async function persist(extra = {}) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const body = {
        model: settings.model,
        effort: settings.effort,
        ats_target_score: Number(settings.ats_target_score),
        max_tailor_passes: Number(settings.max_tailor_passes),
        generate_cover_letter: settings.generate_cover_letter,
        auto_accept_agreements: settings.auto_accept_agreements,
        adopt_missing_skills: settings.adopt_missing_skills,
        resume_page_target: Number(settings.resume_page_target),
        browser_channel: settings.browser_channel,
        target_roles: (settings.target_roles || []).filter((r) => r && r.trim()),
        excluded_terms: (settings.excluded_terms || []).filter((t) => t && t.trim()),
        ...extra,
      };
      const saved = await saveSettings(body);
      setSettings(saved);
      setApiKey("");
      setMessage("Settings saved.");
      onChanged?.(saved);
    } catch (e) {
      setError(requestMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function resetApplications() {
    if (!window.confirm(
      "Clear your entire application history?\n\n" +
      "Deletes every application, its generated resume and cover letter, and its " +
      "match scores, so the Excel export starts empty.\n\n" +
      "Your profile, resume library, jobs and sources are NOT touched.",
    )) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await clearApplications();
      setMessage(result.message);
      onChanged?.();
    } catch (e) {
      setError(requestMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function verify() {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await testApiKey();
      setMessage(
        `Key works. ${result.model} replied "${result.reply}" (${result.input_tokens} in / ${result.output_tokens} out tokens).`,
      );
    } catch (e) {
      setError(requestMessage(e));
    } finally {
      setBusy(false);
    }
  }

  if (!settings) return <div className="empty-state">Loading settings…</div>;

  const keyMissing = !settings.anthropic_api_key_set;

  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <h3>Settings</h3>
          <p>Your API key is stored in backend/settings.json, which is git-ignored.</p>
        </div>
      </div>

      {error && <div className="alert error-alert">{error}</div>}
      {message && <div className="alert approval-alert">{message}</div>}

      {keyMissing && (
        <div className="alert warn-alert">
          <AlertTriangle size={18} />
          <span>
            No Anthropic API key set. Resume tailoring and cover letters need one — get it
            from console.anthropic.com, then paste it below. Job discovery and the Excel
            tracker work without it.
          </span>
        </div>
      )}

      <div className="settings-grid">
        <label className="wide">
          <span className="label-row">
            <KeyRound size={15} /> Anthropic API key
            {settings.anthropic_api_key_set && (
              <em className="ok-chip">
                <CheckCircle2 size={13} /> set ({settings.anthropic_api_key_source}
                {settings.anthropic_api_key_hint && ` ${settings.anthropic_api_key_hint}`})
              </em>
            )}
          </span>
          <input
            type="password"
            placeholder={settings.anthropic_api_key_set ? "Enter a new key to replace" : "sk-ant-…"}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            autoComplete="off"
          />
        </label>

        <label>
          Model
          <select value={settings.model} onChange={(e) => change("model", e.target.value)}>
            <option value="claude-opus-5">Claude Opus 5</option>
            <option value="claude-sonnet-5">Claude Sonnet 5 (cheaper, faster)</option>
            <option value="claude-haiku-4-5">Claude Haiku 4.5</option>
          </select>
        </label>

        <label>
          Effort
          <select disabled={settings.model.startsWith("claude-haiku-4-5")} value={settings.effort} onChange={(e) => change("effort", e.target.value)}>
            <option value="low">Low — fastest</option>
            <option value="medium">Medium</option>
            <option value="high">High — recommended</option>
            <option value="xhigh">Extra high</option>
            <option value="max">Max — slowest, most thorough</option>
          </select>
        </label>

        <label>
          Target job-match score
          <input
            type="number"
            min="50"
            max="100"
            value={settings.ats_target_score}
            onChange={(e) => change("ats_target_score", e.target.value)}
          />
          <small>
            When a draft scores below this, another tailoring pass runs. It is this
            app&apos;s own measure of resume-to-posting fit — no employer publishes a
            cutoff, so there is no &ldquo;right&rdquo; number here.
          </small>
        </label>

        <label>
          Resume length
          <select
            value={settings.resume_page_target ?? 1}
            onChange={(e) => change("resume_page_target", Number(e.target.value))}
          >
            <option value={1}>One page — the default for industry roles</option>
            <option value={2}>Two pages</option>
            <option value={3}>Three pages — academic or senior applications</option>
          </select>
          <small>
            A tailored resume is cut to this. If reaching it would mean deleting
            evidence the posting asks for, it runs one page longer instead.
          </small>
        </label>

        <label>
          Max tailoring passes
          <input
            type="number"
            min="1"
            max="4"
            value={settings.max_tailor_passes}
            onChange={(e) => change("max_tailor_passes", e.target.value)}
          />
          <small>Each extra pass costs another model call but can raise the score.</small>
        </label>

        <label className="wide">
          Target roles — one per line, used to score how well each job matches you
          <textarea
            rows={3}
            placeholder={"Machine Learning Engineer\nAI Engineer\nSoftware Engineer"}
            value={(settings.target_roles || []).join("\n")}
            onChange={(e) => change("target_roles", e.target.value.split("\n"))}
          />
          <small>
            Job titles you are aiming for. Combined with your profile skills, these drive the
            Match % column. Saving re-scores every job.
          </small>
        </label>

        <label className="wide">
          Exclude jobs whose title contains — one per line
          <textarea
            rows={2}
            placeholder={"Sales\nRecruiter\nAccount Manager"}
            value={(settings.excluded_terms || []).join("\n")}
            onChange={(e) => change("excluded_terms", e.target.value.split("\n"))}
          />
        </label>

        <label>
          Browser used for sign-in &amp; autofill
          <select
            value={settings.browser_channel || "chrome"}
            onChange={(e) => change("browser_channel", e.target.value)}
          >
            <option value="chrome">Your installed Chrome — needed for Google sign-in</option>
            <option value="msedge">Microsoft Edge</option>
            <option value="chromium">Bundled Chromium — Google blocks sign-in</option>
          </select>
          <small>
            Chrome and Edge share one saved profile, so switching between them makes the
            next launch slower while the profile is converted. If the browser that opens
            is not the one chosen here, the Sources tab says which opened and why.
          </small>
        </label>

        <label className="check-label wide">
          <input
            type="checkbox"
            checked={settings.generate_cover_letter}
            onChange={(e) => change("generate_cover_letter", e.target.checked)}
          />
          Also generate a tailored cover letter for each application
        </label>

        <label className="check-label wide">
          <input
            type="checkbox"
            checked={settings.adopt_missing_skills}
            onChange={(e) => change("adopt_missing_skills", e.target.checked)}
          />
          <span>
            Include verified profile skills missing from my resume
            <small>
              Include a skill requested by the job only when your resume or confirmed
              profile supports it. Job requirements alone never become candidate facts.
              Review any pending skill suggestions under Profile before generating.
            </small>
          </span>
        </label>

        <label className="check-label wide">
          <input
            type="checkbox"
            checked={settings.auto_accept_agreements}
            onChange={(e) => change("auto_accept_agreements", e.target.checked)}
          />
          <span>
            Auto-accept arbitration agreements and AI-policy acknowledgments
            <small>
              These are binding terms and they differ between employers. Criminal-history
              and background-check questions are never auto-answered regardless of this
              setting — those are statements of fact, not terms.
            </small>
          </span>
        </label>
      </div>

      <div className="agent-actions">
        <button
          className="primary-button"
          disabled={busy}
          onClick={() => persist(apiKey.trim() ? { anthropic_api_key: apiKey.trim() } : {})}
        >
          {busy ? "Saving…" : "Save settings"}
        </button>
        <button
          className="secondary-button"
          disabled={busy || keyMissing}
          onClick={verify}
        >
          Test API key
        </button>
      </div>

      <section className="review-section danger-zone">
        <h3>Start over</h3>
        <p className="section-note">
          Empties the Excel tracker and releases the jobs attached to those applications.
          Your profile, resume library, jobs and sources are never affected.
        </p>
        <div className="agent-actions">
          <button className="cancel-button" disabled={busy} onClick={resetApplications}>
            <Trash2 size={14} /> Clear application history
          </button>
        </div>

      </section>
    </section>
  );
}
