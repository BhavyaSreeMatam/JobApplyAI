import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bot,
  BriefcaseBusiness,
  CheckCircle2,
  Download,
  Gauge,
  RefreshCw,
  Search,
  Settings as SettingsIcon,
  FileText,
  Globe,
  Trash2,
  UserRound,
} from "lucide-react";

import ApplyModal from "./ApplyModal";
import DateRefresh from "./DateRefresh";
import CandidateProfile from "./CandidateProfile";
import SettingsPanel from "./SettingsPanel";
import SourcesPanel from "./SourcesPanel";
import ResumesPanel from "./ResumesPanel";
import {
  bulkDeleteJobs,
  excelDownloadUrl,
  getApplications,
  getHealth,
  getJob,
  getJobs,
  requestMessage,
  syncAllSources,
} from "./api";
import "./App.css";
import "./Agent.css";

const STATUS_LABELS = {
  preparing: "Preparing",
  ready_for_review: "Ready",
  approved_queued: "Queued",
  applying: "Applying",
  needs_human_input: "Needs input",
  applied: "Applied",
  submission_failed: "Failed",
  rejected_by_user: "Rejected",
  interview: "Interview",
  offer: "Offer",
  employer_rejected: "Employer rejected",
  withdrawn: "Withdrawn",
  position_closed: "Closed",
};

// Postings are dated by the day at best - several boards only report "Today" or
// "30+ days ago" - so anything finer than a day would be invented precision.
function timeAgo(value) {
  if (!value) return "—";
  const then = new Date(value);
  if (Number.isNaN(then.getTime())) return "—";
  const days = Math.max(0, Math.floor((Date.now() - then.getTime()) / 86400000));
  if (days === 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 7) return `${days}d ago`;
  if (days < 31) return `${Math.round(days / 7)}w ago`;
  if (days < 365) return `${Math.round(days / 30)}mo ago`;
  const years = Math.round(days / 365);
  return `${years}y ago`;
}

// Anything past this is stale enough to be worth flagging.
function postedClass(value) {
  if (!value) return "posted-unknown";
  const days = (Date.now() - new Date(value).getTime()) / 86400000;
  if (days <= 7) return "posted-fresh";
  if (days <= 30) return "posted-recent";
  return "posted-old";
}

const TABS = [
  { id: "jobs", label: "Jobs", icon: BriefcaseBusiness },
  { id: "sources", label: "Sources", icon: Globe },
  { id: "resumes", label: "Resumes", icon: FileText },
  { id: "profile", label: "Profile", icon: UserRound },
  { id: "settings", label: "Settings", icon: SettingsIcon },
];

export default function App() {
  const [tab, setTab] = useState("jobs");
  const [jobs, setJobs] = useState([]);
  const [applications, setApplications] = useState([]);
  const [health, setHealth] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [query, setQuery] = useState("");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [sortBy, setSortBy] = useState("newest");
  const [limit, setLimit] = useState(100);
  const [selected, setSelected] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [jobData, applicationData, healthData] = await Promise.all([
        getJobs(),
        getApplications(),
        getHealth().catch(() => null),
      ]);
      setJobs(jobData);
      setApplications(applicationData);
      setHealth(healthData);
      setError("");
    } catch (e) {
      setError(requestMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Initial synchronization with the API; later refreshes use the same loader.
    // oxlint-disable-next-line react/set-state-in-effect
    load();
  }, [load]);

  const applicationByJob = useMemo(
    () => Object.fromEntries(applications.map((a) => [a.job_id, a])),
    [applications],
  );

  async function openImportedJob(jobId) {
    try {
      const job = await getJob(jobId);
      setSelected({ job, application: applicationByJob[job.id] });
      setTab("jobs");
    } catch (e) {
      setError(requestMessage(e));
    }
  }

  const metrics = useMemo(
    () => ({
      jobs: jobs.length,
      prepared: applications.filter((a) => ["ready_for_review", "preparing"].includes(a.status)).length,
      applied: applications.filter((a) => a.status === "applied").length,
      sources: new Set(jobs.map((j) => j.source)).size,
    }),
    [jobs, applications],
  );

  const sourceCounts = useMemo(() => {
    const counts = {};
    jobs.forEach((job) => {
      counts[job.source] = (counts[job.source] || 0) + 1;
    });
    return counts;
  }, [jobs]);

  const matchingJobs = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const filtered = jobs.filter((job) => {
      if (sourceFilter !== "all" && job.source !== sourceFilter) return false;
      if (!needle) return true;
      return (
        job.title.toLowerCase().includes(needle) ||
        job.company.toLowerCase().includes(needle) ||
        (job.location || "").toLowerCase().includes(needle)
      );
    });
    // Newest first by default: sorting by match score buried everything freshly
    // synced, because new jobs have no score until Matching is run.
    filtered.sort((a, b) =>
      sortBy === "match"
        ? (b.match_score ?? -1) - (a.match_score ?? -1)
        : new Date(b.posted_at || b.discovered_at) - new Date(a.posted_at || a.discovered_at),
    );
    return filtered;
  }, [jobs, query, sourceFilter, sortBy]);

  const visibleJobs = useMemo(() => matchingJobs.slice(0, limit), [matchingJobs, limit]);

  async function removeSource(source) {
    const count = sourceCounts[source] || 0;
    if (!window.confirm(
      `Remove ${count} ${source} jobs from the dashboard?

` +
      "Jobs you have already applied to or started are kept.",
    )) return;
    setError("");
    try {
      const result = await bulkDeleteJobs({ source });
      await load();
      setSourceFilter("all");
      setError(result.deleted ? "" : "Nothing removed — those jobs all have applications.");
    } catch (e) {
      setError(requestMessage(e));
    }
  }

  async function syncAll() {
    setSyncing(true);
    setError("");
    try {
      const result = await syncAllSources(false);
      await load();
      // A source that failed used to be invisible: the totals simply came back
      // lower and nothing said which site had not been read, so a broken
      // LinkedIn session looked like a quiet week for jobs.
      const failures = result.failures || [];
      if (failures.length) {
        setError(
          `${failures.length} of ${result.sources_synced} source(s) failed — ` +
          failures
            .map((f) => `${f.label || f.platform || "a source"}: ${f.error}`)
            .join(" · "),
        );
      } else if (result.sources_synced === 0) {
        setError("No sources configured yet — add a company careers page under Sources.");
      } else {
        setError("");
      }
    } catch (e) {
      setError(requestMessage(e));
    } finally {
      setSyncing(false);
    }
  }

  const keyMissing = health && health.claude_key === "missing";

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-icon"><Bot size={24} /></div>
          <div>
            <h1>JobApplyAI</h1>
          </div>
        </div>

        <nav className="tabs">
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              className={`tab ${tab === id ? "active" : ""}`}
              onClick={() => setTab(id)}
            >
              <Icon size={15} /> {label}
            </button>
          ))}
        </nav>

        <div className="header-actions">
          <button className="secondary-button" onClick={syncAll} disabled={syncing}>
            <RefreshCw size={15} className={syncing ? "spin" : ""} />
            {syncing ? "Syncing…" : "Sync jobs"}
          </button>
          <a className="primary-button" href={excelDownloadUrl}>
            <Download size={15} /> Excel
          </a>
        </div>
      </header>

      <main className="dashboard">
        {error && <div className="alert error-alert">{error}</div>}

        {keyMissing && tab !== "settings" && (
          <div className="alert warn-alert">
            <span>
              No Anthropic API key set — resume tailoring is disabled until you add one.
            </span>
            <button className="table-button" onClick={() => setTab("settings")}>
              Add key
            </button>
          </div>
        )}

        {tab === "jobs" && (
          <>
            <section className="metrics-grid">
              <MetricCard label="Jobs discovered" value={metrics.jobs} icon={<BriefcaseBusiness />} />
              <MetricCard label="Source platforms" value={metrics.sources} icon={<Globe />} />
              <MetricCard label="Packages in progress" value={metrics.prepared} icon={<Gauge />} />
              <MetricCard label="Applications sent" value={metrics.applied} icon={<CheckCircle2 />} />
            </section>

            <section className="panel">
              <div className="panel-heading">
                <div>
                  <h3>Jobs</h3>
                  <p>Click Apply to generate a resume tailored to that specific posting.</p>
                </div>
                <div className="job-controls">
                  <label className="search-box">
                    <Search size={15} />
                    <input
                      placeholder="Search title, company, location"
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                    />
                  </label>
                  <select value={sourceFilter} onChange={(e) => setSourceFilter(e.target.value)}>
                    <option value="all">All sources ({jobs.length})</option>
                    {Object.entries(sourceCounts)
                      .sort((a, b) => b[1] - a[1])
                      .map(([source, count]) => (
                        <option key={source} value={source}>
                          {source} ({count})
                        </option>
                      ))}
                  </select>
                  <select value={sortBy} onChange={(e) => setSortBy(e.target.value)}>
                    <option value="newest">Most recently posted</option>
                    <option value="match">Best match first</option>
                  </select>
                </div>
              </div>

              <DateRefresh jobs={jobs} disabled={syncing || loading} onUpdated={load} />

              {loading ? (
                <div className="empty-state">Loading jobs…</div>
              ) : visibleJobs.length === 0 ? (
                <div className="empty-state">
                  {jobs.length === 0
                    ? "No jobs yet. Add a source under the Sources tab, then press Sync jobs."
                    : "No jobs match this filter."}
                </div>
              ) : (
                <div className="table-wrapper">
                  <table>
                    <thead>
                      <tr>
                        <th>Role</th>
                        <th>Location</th>
                        <th>Source</th>
                        <th>Match</th>
                        <th>Posted</th>
                        <th>Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {visibleJobs.map((job) => {
                        const application = applicationByJob[job.id];
                        return (
                          <tr key={job.id}>
                            <td>
                              <strong>{job.title}</strong>
                              <span className="cell-subtitle">{job.company}</span>
                            </td>
                            <td>{job.location || "Not specified"}</td>
                            <td><span className="source-tag">{job.source}</span></td>
                            <td>{job.match_score == null ? "—" : `${Math.round(job.match_score)}%`}</td>
                            <td>
                              <span className={`posted ${postedClass(job.posted_at)}`}>
                                {timeAgo(job.posted_at)}
                              </span>
                              {application && (
                                <span className={`status status-${application.status}`}>
                                  {STATUS_LABELS[application.status] || application.status}
                                </span>
                              )}
                            </td>
                            <td>
                              <button
                                className={application ? "table-button" : "primary-button"}
                                onClick={() => setSelected({ job, application })}
                              >
                                {application?.status === "applied"
                                  ? "View"
                                  : application
                                    ? "Review"
                                    : "Apply"}
                              </button>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                  <div className="list-footer">
                    <span className="hint">
                      Showing {visibleJobs.length} of {matchingJobs.length}
                      {matchingJobs.length !== jobs.length && ` (${jobs.length} total)`}
                    </span>
                    {matchingJobs.length > visibleJobs.length && (
                      <button className="secondary-button" onClick={() => setLimit((n) => n + 200)}>
                        Show 200 more
                      </button>
                    )}
                    {sourceFilter !== "all" && (
                      <button className="cancel-button" onClick={() => removeSource(sourceFilter)}>
                        <Trash2 size={14} /> Remove all {sourceFilter} jobs
                      </button>
                    )}
                  </div>
                </div>
              )}
            </section>
          </>
        )}

        {tab === "sources" && (
          <SourcesPanel
            onJobsChanged={load}
            onOpenJob={openImportedJob}
          />
        )}
        {tab === "resumes" && <ResumesPanel />}
        {tab === "profile" && <CandidateProfile />}
        {tab === "settings" && <SettingsPanel onChanged={load} />}
      </main>

      {selected && (
        <ApplyModal
          job={selected.job}
          application={selected.application}
          onClose={() => setSelected(null)}
          onChanged={load}
        />
      )}
    </div>
  );
}

function MetricCard({ label, value, icon }) {
  return (
    <article className="metric-card">
      <div className="metric-icon">{icon}</div>
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
      </div>
    </article>
  );
}
