import { useEffect, useRef, useState } from "react";
import { BookMarked, Download, FileText, Gauge, Sparkles, Star, Trash2, Upload } from "lucide-react";

import {
  buildResume,
  deleteResume,
  getResumes,
  requestMessage,
  resumeFileUrl,
  scoreResume,
  setMasterResume,
  updateResume,
  uploadResumeToLibrary,
} from "./api";

function scoreClass(score) {
  if (score == null) return "score-none";
  if (score >= 85) return "score-great";
  if (score >= 70) return "score-good";
  if (score >= 55) return "score-fair";
  return "score-poor";
}

export default function ResumesPanel() {
  const [resumes, setResumes] = useState(null);
  const [form, setForm] = useState({ role: "", company: "", job_description: "", label: "" });
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState(null);
  const [checking, setChecking] = useState(null); // {id, role, job_description}
  const fileInput = useRef(null);

  const reload = () => getResumes().then((d) => setResumes(d.resumes)).catch(
    (e) => setNotice({ kind: "error", text: requestMessage(e) }),
  );
  useEffect(() => {
    reload();
  }, []);

  async function run(key, action, done) {
    setBusy(key);
    setNotice(null);
    try {
      const result = await action();
      done?.(result);
      await reload();
    } catch (e) {
      setNotice({ kind: "error", text: requestMessage(e) });
    } finally {
      setBusy("");
    }
  }

  function build() {
    if (!form.role.trim()) return;
    run("build", () => buildResume(form), (r) => {
      setNotice({
        kind: "ok",
        text: `Built "${r.resume.label}" — job match ${r.resume.ats_score} (${r.resume.keyword_coverage}% keyword coverage) after ${r.passes} pass${r.passes === 1 ? "" : "es"}.`,
      });
      setForm({ role: "", company: "", job_description: "", label: "" });
    });
  }

  function upload(file) {
    if (!file) return;
    run("upload", () => uploadResumeToLibrary(file, "", ""), (r) =>
      setNotice({ kind: "ok", text: `Added "${r.resume.label}". Run a match check to score it.` }));
    if (fileInput.current) fileInput.current.value = "";
  }

  function runCheck() {
    if (!checking?.role?.trim()) return;
    run("check", () => scoreResume(checking.id, {
      role: checking.role, job_description: checking.job_description || "",
    }), (r) => {
      setNotice({
        kind: "ok",
        text: `"${r.resume.label}" matches ${checking.role} at ${r.resume.ats_score} — ${r.resume.keyword_coverage}% keyword coverage.`,
      });
      setChecking(null);
    });
  }

  if (!resumes) return <div className="empty-state">Loading resumes…</div>;

  return (
    <>
      {notice && (
        <div className={`alert ${notice.kind === "error" ? "error-alert" : "approval-alert"}`}>
          {notice.text}
        </div>
      )}

      <section className="panel">
        <div className="panel-heading">
          <div>
            <h3><Sparkles size={17} /> Build a resume</h3>
            <p>
              Paste a job description for the sharpest result, or give just a role title for a
              reusable one. Built from your profile, scored, and saved to the library below.
            </p>
          </div>
        </div>

        <div className="candidate-grid">
          <label className="profile-field">
            Target role *
            <input
              placeholder="Machine Learning Engineer"
              value={form.role}
              onChange={(e) => setForm((f) => ({ ...f, role: e.target.value }))}
            />
          </label>
          <label className="profile-field">
            Company (optional)
            <input
              placeholder="Anthropic"
              value={form.company}
              onChange={(e) => setForm((f) => ({ ...f, company: e.target.value }))}
            />
          </label>
          <label className="profile-field">
            Save as (optional)
            <input
              placeholder="Defaults to the role"
              value={form.label}
              onChange={(e) => setForm((f) => ({ ...f, label: e.target.value }))}
            />
          </label>
        </div>

        <label className="profile-field">
          Job description (optional, but gives a much better score)
          <textarea
            rows={7}
            placeholder="Paste the full posting here…"
            value={form.job_description}
            onChange={(e) => setForm((f) => ({ ...f, job_description: e.target.value }))}
          />
        </label>

        <div className="agent-actions">
          <button className="primary-button" disabled={busy === "build" || !form.role.trim()} onClick={build}>
            <Sparkles size={15} /> {busy === "build" ? "Building & scoring…" : "Build resume"}
          </button>
          <label className="secondary-button upload-inline">
            <Upload size={15} /> {busy === "upload" ? "Uploading…" : "Upload my own resume"}
            <input
              ref={fileInput}
              type="file"
              accept=".pdf,.docx,.txt,.md"
              disabled={Boolean(busy)}
              onChange={(e) => upload(e.target.files?.[0])}
            />
          </label>
        </div>
      </section>

      <section className="panel">
        <div className="panel-heading">
          <div>
            <h3><FileText size={17} /> My resumes</h3>
            <p>
              Pick any of these when applying instead of generating a new one. Uploaded files
              work the same as built ones.
            </p>
          </div>
          <span className="count-chip">{resumes.length} saved</span>
        </div>

        {resumes.length === 0 ? (
          <div className="empty-state">
            Nothing saved yet. Build one above, or upload a resume you already have.
          </div>
        ) : (
          <div className="resume-grid">
            {resumes.map((item) => (
              <article className="resume-card" key={item.id}>
                <div className="resume-card-top">
                  <div className={`resume-score ${scoreClass(item.ats_score)}`}>
                    {item.ats_score == null ? "—" : Math.round(item.ats_score)}
                    <span>Match</span>
                  </div>
                  <div className="resume-meta">
                    <strong>{item.label || "Untitled"}</strong>
                    <span>
                      {item.role_target || "No role set"}
                      {item.company && ` · ${item.company}`}
                    </span>
                    <span className="resume-sub">
                      {item.kind === "uploaded" ? "Uploaded" : "Built here"}
                      {item.times_used > 0 && ` · used ${item.times_used}×`}
                      {` · ${new Date(item.created_at).toLocaleDateString()}`}
                    </span>
                  </div>
                  <button
                    className={`icon-button ${item.is_favourite ? "starred" : ""}`}
                    title={item.is_favourite ? "Unpin" : "Pin to top"}
                    onClick={() => run(item.id, () => updateResume(item.id, { is_favourite: !item.is_favourite }))}
                  >
                    <Star size={16} />
                  </button>
                </div>

                {item.is_master ? (
                  <p className="master-chip">
                    <BookMarked size={14} /> Master resume — every application starts from this
                    file and only re-words its sentences.
                  </p>
                ) : (
                  <button
                    className="table-button master-button"
                    disabled={busy === item.id}
                    onClick={() => run(item.id, () => setMasterResume(item.id))}
                  >
                    <BookMarked size={14} /> Use as master
                  </button>
                )}

                {item.matched?.length > 0 && (
                  <div className="chip-row">
                    {item.matched.slice(0, 10).map((term) => (
                      <span className="chip chip-ok" key={term}>{term}</span>
                    ))}
                  </div>
                )}
                {item.missing?.length > 0 && (
                  <div className="chip-row">
                    {item.missing.slice(0, 6).map((term) => (
                      <span className="chip chip-required" key={term}>{term}</span>
                    ))}
                  </div>
                )}

                <div className="resume-actions">
                  <a className="table-button" href={resumeFileUrl(item.id)} target="_blank" rel="noreferrer">
                    <Download size={14} /> PDF
                  </a>
                  <button
                    className="table-button"
                    onClick={() => setChecking({ id: item.id, role: item.role_target || "", job_description: "" })}
                  >
                    <Gauge size={14} /> Match check
                  </button>
                  <button
                    className="cancel-button"
                    disabled={busy === item.id}
                    onClick={() => run(item.id, () => deleteResume(item.id))}
                    aria-label="Delete resume"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>

                {checking?.id === item.id && (
                  <div className="check-box">
                    <label className="profile-field">
                      Score this resume for which role?
                      <input
                        autoFocus
                        placeholder="Backend Engineer"
                        value={checking.role}
                        onChange={(e) => setChecking((c) => ({ ...c, role: e.target.value }))}
                      />
                    </label>
                    <label className="profile-field">
                      Job description (optional — far more accurate with it)
                      <textarea
                        rows={4}
                        value={checking.job_description}
                        onChange={(e) => setChecking((c) => ({ ...c, job_description: e.target.value }))}
                      />
                    </label>
                    <div className="agent-actions">
                      <button className="primary-button" disabled={busy === "check" || !checking.role.trim()} onClick={runCheck}>
                        {busy === "check" ? "Scoring…" : "Run match check"}
                      </button>
                      <button className="secondary-button" onClick={() => setChecking(null)}>Cancel</button>
                    </div>
                  </div>
                )}
              </article>
            ))}
          </div>
        )}
      </section>
    </>
  );
}
