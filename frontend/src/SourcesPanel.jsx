import { useEffect, useState } from "react";
import {
  Building2,
  Link2,
  LogIn,
  MonitorSmartphone,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react";

import {
  addBrowserSource,
  addCareersPage,
  addSingleJob,
  deleteSource,
  getSources,
  openBrowser,
  requestMessage,
  syncSource,
  updateSource,
} from "./api";

const BROWSER_LABELS = { linkedin: "LinkedIn", indeed: "Indeed", handshake: "Handshake" };
const BROWSER_NAMES = { chrome: "Chrome", msedge: "Microsoft Edge", chromium: "Chromium" };

export default function SourcesPanel({ onJobsChanged, onOpenJob }) {
  const [data, setData] = useState(null);
  const [jobUrl, setJobUrl] = useState("");
  const [careersUrl, setCareersUrl] = useState("");
  const [careersLabel, setCareersLabel] = useState("");
  const [browserForm, setBrowserForm] = useState({
    platform: "linkedin",
    search_terms: "",
    location_filter: "",
  });
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState({});   // {section: {kind, text}}

  const say = (section, kind, text) => setNotice({ [section]: { kind, text } });

  const reload = () => getSources().then(setData).catch((e) => say("job", "error", requestMessage(e)));
  useEffect(() => {
    reload();
  }, []);

  async function run(key, section, action, onDone) {
    setBusy(key);
    setNotice({});
    if (section === "browser" && String(key).length > 10) {
      // Browser syncs visit each job page at human pace, so they take minutes.
      // Without saying so, a working sync is indistinguishable from a hang.
      say("browser", "ok",
        "Reading jobs in the browser window — this takes a few minutes, as each " +
        "posting is opened at human pace. Leave the window open.");
    }
    try {
      const result = await action();
      onDone?.(result);
      await reload();
      onJobsChanged?.();
      return result;
    } catch (e) {
      say(section, "error", requestMessage(e));
    } finally {
      setBusy("");
    }
  }

  async function importJob() {
    const url = jobUrl.trim();
    if (!url) return;
    await run("job", "job", () => addSingleJob(url), (result) => {
      setJobUrl("");
      say("job", "ok",
        result.already_known
          ? `Already on your dashboard: ${result.title} at ${result.company}. Opening it…`
          : `Added ${result.title} at ${result.company}. Opening it…`,
      );
      onOpenJob?.(result.job_id);
    });
  }

  async function addCareers() {
    const url = careersUrl.trim();
    if (!url) return;
    await run("careers", "careers", () => addCareersPage(url, careersLabel.trim()), (result) => {
      setCareersUrl("");
      setCareersLabel("");
      say("careers", "ok",
        `Added ${result.source.label} — ${result.jobs_available} jobs available via ${result.source.platform}. Press Sync jobs to pull them in.`,
      );
    });
  }

  if (!data) return (
    <div className="empty-state">
      {notice.job?.kind === "error" ? (
        <>
          <Notice item={notice.job} />
          <button className="secondary-button" onClick={() => { setNotice({}); reload(); }}>
            Retry
          </button>
        </>
      ) : "Loading sources…"}
    </div>
  );

  const boardSources = data.sources.filter((s) => s.kind === "board");
  const browserSources = data.sources.filter((s) => s.kind === "browser");

  return (
    <>
      {/* ---------------------------- one job ---------------------------- */}
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h3><Link2 size={17} /> Apply to one job</h3>
            <p>
              Paste any job link — a job board, a company careers page, anywhere. The posting
              is read, added to your dashboard, and opened ready to generate your resume.
            </p>
          </div>
        </div>
        <div className="add-source-row">
          <input
            placeholder="https://…  paste a single job posting URL"
            value={jobUrl}
            onChange={(e) => setJobUrl(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && importJob()}
          />
          <button className="primary-button" disabled={busy === "job" || !jobUrl.trim()} onClick={importJob}>
            <Plus size={15} /> {busy === "job" ? "Reading job…" : "Add & apply"}
          </button>
        </div>
        <Notice item={notice.job} />
      </section>

      {/* ------------------------- career pages -------------------------- */}
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h3><Building2 size={17} /> Company career pages</h3>
            <p>
              Add a company once and every job it posts appears on your dashboard. Works when
              the company uses Workday, Greenhouse, Lever, Ashby, Workable, SmartRecruiters or
              Recruitee. Companies running their own site (Google, Meta, TikTok) publish no
              readable list — paste their job links into <em>Apply to one job</em> instead.
            </p>
          </div>
          <span className="count-chip">{boardSources.length} added</span>
        </div>

        <div className="add-source-row">
          <input
            placeholder="https://jobs.lever.co/company or a supported company careers URL"
            value={careersUrl}
            onChange={(e) => setCareersUrl(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && addCareers()}
          />
          <input
            className="narrow"
            placeholder="Company name"
            value={careersLabel}
            onChange={(e) => setCareersLabel(e.target.value)}
          />
          <button className="primary-button" disabled={busy === "careers" || !careersUrl.trim()} onClick={addCareers}>
            <Plus size={15} /> {busy === "careers" ? "Detecting…" : "Add company"}
          </button>
        </div>
        <Notice item={notice.careers} />
        {boardSources.length === 0 ? (
          <div className="empty-state">
            No companies yet. Add one above and its jobs land on your dashboard.
          </div>
        ) : (
          <div className="source-list">
            {boardSources.map((source) => (
              <SourceRow
                key={source.id}
                source={source}
                busy={busy}
                onSync={() => run(source.id, source.kind === "browser" ? "browser" : "careers", () => syncSource(source.id), (r) =>
                  say("careers", "ok", `${r.label}: ${r.added} new, ${r.updated} updated of ${r.found} found.`))}
                onToggle={() => run(source.id, source.kind === "browser" ? "browser" : "careers", () => updateSource(source.id, { enabled: !source.enabled }))}
                onDelete={() => run(source.id, source.kind === "browser" ? "browser" : "careers", () => deleteSource(source.id), () => say("careers", "ok", "Company removed."))}
              />
            ))}
          </div>
        )}
      </section>

      {/* ------------------------ browser sources ------------------------ */}
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h3><MonitorSmartphone size={17} /> LinkedIn, Indeed &amp; Handshake</h3>
            <p>
              These publish no feed, so their jobs are read from a real browser window you
              sign into — the same way you would browse them yourself.
            </p>
            <ol className="how-it-works">
              <li>Save a search below (role + location) — like typing it into LinkedIn.</li>
              <li>Press <strong>Sign in</strong> on its row. A browser opens; log in once.
                  The login lives in that browser, never in this app.</li>
              <li>Press <strong>Sync</strong>, or <strong>Sync jobs</strong> in the header. The
                  browser opens your search, reads the results, and adds them to your dashboard.</li>
            </ol>
          </div>
          <span className="count-chip">{browserSources.length} added</span>
        </div>

        {data.browser_substituted && (
          <p className="alert warn-alert">
            <strong>{BROWSER_NAMES[data.browser_in_use] || data.browser_in_use} is open,
            not {BROWSER_NAMES[data.browser_wanted] || data.browser_wanted}.</strong>{" "}
            {data.browser_note} Close the browser and sign in again to retry your choice.
          </p>
        )}

        <div className="add-source-row">
          <select
            className="narrow"
            value={browserForm.platform}
            onChange={(e) => setBrowserForm((f) => ({ ...f, platform: e.target.value }))}
          >
            {data.browser_platforms.map((p) => (
              <option key={p} value={p}>{BROWSER_LABELS[p] || p}</option>
            ))}
          </select>
          <input
            placeholder="Search terms, e.g. machine learning engineer new grad"
            value={browserForm.search_terms}
            onChange={(e) => setBrowserForm((f) => ({ ...f, search_terms: e.target.value }))}
          />
          <input
            className="narrow"
            placeholder="Location"
            value={browserForm.location_filter}
            onChange={(e) => setBrowserForm((f) => ({ ...f, location_filter: e.target.value }))}
          />
          <button
            className="primary-button"
            disabled={busy === "addb" || !browserForm.search_terms.trim()}
            onClick={() =>
              run("addb", "browser", () => addBrowserSource({
                ...browserForm,
                label: BROWSER_LABELS[browserForm.platform] || browserForm.platform,
              }), () => {
                setBrowserForm((f) => ({ ...f, search_terms: "" }));
                say("browser", "ok", "Search added. Sign in on its row, then press Sync.");
              })
            }
          >
            <Plus size={15} /> Add search
          </button>
        </div>
        <Notice item={notice.browser} />

        {browserSources.length === 0 ? (
          <div className="empty-state">No searches yet.</div>
        ) : (
          <div className="source-list">
            {browserSources.map((source) => (
              <SourceRow
                key={source.id}
                source={source}
                busy={busy}
                onSignIn={() => run(`in${source.id}`, "browser", () => openBrowser(source.platform), () =>
                  say("browser", "ok", `Sign in to ${source.label} in the browser window, then press Sync.`))}
                onSync={() => run(source.id, source.kind === "browser" ? "browser" : "careers", () => syncSource(source.id), (r) =>
                  say("browser", "ok", `${r.label}: ${r.added} new of ${r.found} found.`))}
                onToggle={() => run(source.id, source.kind === "browser" ? "browser" : "careers", () => updateSource(source.id, { enabled: !source.enabled }))}
                onDelete={() => run(source.id, source.kind === "browser" ? "browser" : "careers", () => deleteSource(source.id), () => say("browser", "ok", "Search removed."))}
              />
            ))}
          </div>
        )}
      </section>

    </>
  );
}

function Notice({ item }) {
  if (!item) return null;
  return (
    <div role={item.kind === "error" ? "alert" : "status"}
      className={`alert ${item.kind === "error" ? "error-alert" : "approval-alert"}`}>
      {item.text}
    </div>
  );
}

function SourceRow({ source, busy, onSync, onToggle, onDelete, onSignIn }) {
  const working = busy === source.id;
  const needsSignIn = /not signed in|sign in|login|challenge/i.test(source.last_error || "");
  return (
    <article className={`source-row ${source.enabled ? "" : "disabled"}`}>
      <div className="source-main">
        <strong>{source.label || source.slug}</strong>
        <span className="source-meta">
          {source.platform}
          {source.slug && ` · ${source.slug}`}
          {source.search_terms && ` · “${source.search_terms}”`}
          {source.location_filter && ` · ${source.location_filter}`}
        </span>
        {source.last_error ? (
          <span className="source-error">{source.last_error}</span>
        ) : (
          <span className="source-meta">
            {source.last_synced_at
              ? `${source.last_sync_added} new of ${source.last_sync_found} found · ${new Date(source.last_synced_at).toLocaleString()}`
              : "Not synced yet"}
          </span>
        )}
      </div>
      <div className="source-actions">
        {onSignIn && (
          <button
            className={needsSignIn ? "primary-button" : "table-button"}
            disabled={busy === `in${source.id}`}
            onClick={onSignIn}
          >
            <LogIn size={14} /> Sign in
          </button>
        )}
        <button className="table-button" disabled={working} onClick={onSync}>
          <RefreshCw size={14} className={working ? "spin" : ""} />
          {working ? "Syncing…" : "Sync"}
        </button>
        <button className="table-button" disabled={working} onClick={onToggle}>
          {source.enabled ? "Disable" : "Enable"}
        </button>
        <button className="cancel-button" disabled={working} onClick={onDelete} aria-label="Remove">
          <Trash2 size={14} />
        </button>
      </div>
    </article>
  );
}
