import { useEffect, useRef, useState } from "react";
import { X, ExternalLink, FileText, Sparkles, MousePointerClick, CheckCircle2, MessageSquare, Upload, BookMarked } from "lucide-react";

import {
  autofillCurrentPage,
  coverLetterUrl,
  draftAnswers,
  getJob,
  getPackage,
  markApplied,
  prepareApplication,
  requestMessage,
  resumeUrl,
  fetchJobDescription,
  getResumes,
  runAutofill,
  setJobDescription,
  saveAnswers,
  uploadResumeToLibrary,
  attachLibraryResume,
} from "./api";

// Word count is guidance; the backend validates the actual posting content.
function countWords(text) {
  return (text || "").trim() ? (text || "").trim().split(/\s+/).length : 0;
}

function scoreClass(score) {
  if (score == null) return "score-none";
  if (score >= 85) return "score-great";
  if (score >= 70) return "score-good";
  if (score >= 55) return "score-fair";
  return "score-poor";
}

export default function ApplyModal({ job, application, onClose, onChanged }) {
  if (!job) return null;
  return (
    <Body key={job.id} job={job} application={application} onClose={onClose} onChanged={onChanged} />
  );
}

function Body({ job: initialJob, application, onClose, onChanged }) {
  // The modal keeps its own live copy. It used to render the object the list
  // handed it at open time, so a description recovered here was saved on the
  // server and never seen: the panel below still showed the empty string the
  // list was holding, and tailoring was handed that same stale object.
  const [job, setJob] = useState(initialJob);
  const [pkg, setPkg] = useState(null);
  const [loading, setLoading] = useState(Boolean(application));
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [autofill, setAutofill] = useState(null);
  const [confirmationId, setConfirmationId] = useState("");
  const [drafts, setDrafts] = useState(null);
  const [library, setLibrary] = useState(null);
  const [needsDescription, setNeedsDescription] = useState(!(initialJob.description || "").trim());
  const [pastedDescription, setPastedDescription] = useState("");
  const resumeInput = useRef(null);

  useEffect(() => {
    if (!application) {
      return;
    }
    let cancelled = false;
    getPackage(application.id)
      .then((data) => !cancelled && setPkg(data))
      .catch((e) => !cancelled && setError(requestMessage(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [application]);

  async function generate(regenerate) {
    setBusy("generate");
    setError("");
    setMessage("");
    try {
      // Always tailor against the stored record, never against whatever this
      // modal was opened with. The two diverge as soon as a description is
      // recovered, and the stale one is the empty one.
      const latest = await refreshJob();
      if (!(latest.description || "").trim()) {
        setNeedsDescription(true);
        throw new Error(
          "No description has been captured for this job yet. Read it in your " +
          "browser or paste it below, then tailoring can run against it.",
        );
      }
      const data = await prepareApplication(job.id, regenerate);
      setPkg(data);
      setNeedsDescription(false);
      setMessage(
        `Resume tailored. Job match ${data.ats_score} (keyword coverage ${data.keyword_coverage}%) after ${data.passes_used} pass${data.passes_used === 1 ? "" : "es"}.`,
      );
      onChanged?.();
    } catch (e) {
      const text = requestMessage(e);
      setError(text);
      // A posting whose description was never captured is fixable, and saying
      // only "not enough to tailor against" leaves the user stuck on a job they
      // can see the description of in their own browser.
      setNeedsDescription((previous) =>
        previous || /no description|not a job posting|does not read like|paste the full posting|sign-in or verification wall|placeholder/i.test(text),
      );
    } finally {
      setBusy("");
    }
  }

  async function refreshJob() {
    const latest = await getJob(job.id);
    setJob(latest);
    return latest;
  }

  async function grabDescription() {
    setBusy("description");
    setError("");
    try {
      const result = await fetchJobDescription(job.id);
      setMessage(result.message);
      setNeedsDescription(false);
      await refreshJob();
      onChanged?.();
      await generate(true);
    } catch (e) {
      setError(requestMessage(e));
    } finally {
      setBusy("");
    }
  }

  async function saveDescription() {
    setBusy("description");
    setError("");
    try {
      const result = await setJobDescription(job.id, pastedDescription);
      setMessage(result.message);
      setNeedsDescription(false);
      setPastedDescription("");
      await refreshJob();
      onChanged?.();
      await generate(true);
    } catch (e) {
      setError(requestMessage(e));
    } finally {
      setBusy("");
    }
  }

  async function openLibrary() {
    setBusy("library");
    setError("");
    try {
      const data = await getResumes();
      setLibrary(data.resumes);
      if (data.resumes.length === 0) {
        setMessage("Your resume library is empty — build or upload one in the Resumes tab.");
      }
    } catch (e) {
      setError(requestMessage(e));
    } finally {
      setBusy("");
    }
  }

  async function attachResume(resumeId) {
    setBusy("attach");
    setError("");
    setMessage("");
    try {
      const result = await attachLibraryResume(job.id, resumeId);
      setLibrary(null);
      const refreshed = await getPackage(result.application_id);
      setPkg(refreshed);
      setMessage(`Attached "${result.used.label}". ${result.note}`);
      onChanged?.();
    } catch (e) {
      setError(requestMessage(e));
    } finally {
      setBusy("");
    }
  }

  // Upload a file and attach it to this job in one step.
  async function uploadAndAttach(file) {
    if (!file) return;
    setBusy("upload");
    setError("");
    setMessage("");
    try {
      const added = await uploadResumeToLibrary(file, file.name.replace(/\.[^.]+$/, ""), job.title);
      const result = await attachLibraryResume(job.id, added.resume.id);
      setLibrary(null);
      const refreshed = await getPackage(result.application_id);
      setPkg(refreshed);
      setMessage(`Uploaded and attached "${added.resume.label}". It is saved in your Resumes library too.`);
      onChanged?.();
    } catch (e) {
      setError(requestMessage(e));
    } finally {
      setBusy("");
      if (resumeInput.current) resumeInput.current.value = "";
    }
  }

  async function draft() {
    setBusy("draft");
    setError("");
    setMessage("");
    try {
      const result = await draftAnswers(pkg.application_id);
      setDrafts(result.drafted);
      setMessage(result.message);
    } catch (e) {
      setError(requestMessage(e));
    } finally {
      setBusy("");
    }
  }

  async function storeAnswers() {
    setBusy("saveanswers");
    setError("");
    try {
      const payload = Object.fromEntries(
        drafts.filter((d) => (d.answer || "").trim()).map((d) => [d.field_name, d.answer]),
      );
      const result = await saveAnswers(pkg.application_id, payload);
      setMessage(`Saved ${result.saved} answers. They will be filled on the next run.`);
    } catch (e) {
      setError(requestMessage(e));
    } finally {
      setBusy("");
    }
  }

  async function fillForm() {
    setBusy("autofill");
    setError("");
    setMessage("");
    try {
      const report = await runAutofill(pkg.application_id);
      setAutofill(report);
      setMessage(
        `Filled ${report.filled_count} fields in the browser. Review everything, then click Submit yourself.`,
      );
      onChanged?.();
    } catch (e) {
      setError(requestMessage(e));
    } finally {
      setBusy("");
    }
  }

  // For sign-in walls and multi-step forms: fill whatever is on screen now.
  async function fillCurrent() {
    setBusy("fillcurrent");
    setError("");
    setMessage("");
    try {
      const report = await autofillCurrentPage(pkg.application_id);
      setAutofill(report);
      setMessage(
        `Filled ${report.filled_count} fields on "${report.title || report.url}". ` +
        "Move to the next step and run it again if there is one.",
      );
      onChanged?.();
    } catch (e) {
      setError(requestMessage(e));
    } finally {
      setBusy("");
    }
  }

  async function confirmApplied() {
    setBusy("applied");
    try {
      await markApplied(pkg.application_id, confirmationId);
      setMessage("Recorded as applied. It now shows in the Excel tracker.");
      onChanged?.();
    } catch (e) {
      setError(requestMessage(e));
    } finally {
      setBusy("");
    }
  }

  const report = pkg?.score_report || {};
  const missing = report.keyword?.missing || [];
  const matched = report.keyword?.matched || [];
  const eligibility = report.eligibility || {};
  const uncovered = report.match?.components?.responsibilities?.uncovered || [];
  const adopted = pkg?.adopted_skills || [];
  // Present only when the application was tailored from a master resume.
  const tailored = pkg?.tailored_resume || {};
  // Weighted parts of the match score, heaviest first. A part the posting says
  // nothing about carries weight 0 and is shown greyed rather than hidden, so a
  // component scoring 0 is never confused with one that had nothing to measure.
  const components = Object.entries(report.match?.components || {}).sort(
    (a, b) => b[1].weight - a[1].weight,
  );

  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && !busy && onClose()}>
      <section className="review-modal" role="dialog" aria-modal="true" aria-labelledby="apply-title">
        <div className="modal-header">
          <div>
            <span className="eyebrow modal-eyebrow">Apply</span>
            <h2 id="apply-title">{job.title}</h2>
            <p>
              {job.company}
              {job.location ? ` · ${job.location}` : ""} · {job.source}
            </p>
          </div>
          <button className="icon-button" onClick={onClose} disabled={Boolean(busy)} aria-label="Close">
            <X size={20} />
          </button>
        </div>

        <div className="modal-content">
          {error && <p role="alert" className="alert error-alert">{error}</p>}
          {message && <p role="status" className="alert approval-alert">{message}</p>}

          {needsDescription && (
            <div className="alert warn-alert description-fix">
              <strong>Add or recover the job description.</strong>
              <span>
                Tailoring needs the actual posting. Open it in your connected browser
                and read it from there, or paste the responsibilities and requirements below.
              </span>
              <div className="agent-actions">
                <a
                  className="secondary-button"
                  href={job.application_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  <ExternalLink size={15} /> Open the posting
                </a>
                <button
                  className="secondary-button"
                  disabled={Boolean(busy)}
                  onClick={grabDescription}
                >
                  {busy === "description" ? "Reading…" : "Read in my browser & tailor"}
                </button>
              </div>
              <textarea
                rows={4}
                placeholder="…or paste the full description here"
                value={pastedDescription}
                onChange={(e) => setPastedDescription(e.target.value)}
              />
              <button
                className="primary-button"
                disabled={Boolean(busy) || !pastedDescription.trim()}
                onClick={saveDescription}
              >
                Save description &amp; tailor
                {pastedDescription.trim() ? ` (${countWords(pastedDescription)} words)` : ""}
              </button>
            </div>
          )}

          {loading ? (
            <div className="empty-state">Loading…</div>
          ) : !pkg ? (
            <div className="generate-cta">
              <Sparkles size={30} />
              <h3>Generate your tailored application</h3>
              <p>
                Claude reads this job description, breaks out what it screens for, and
                rewrites your resume from your verified profile to maximise keyword coverage —
                without inventing anything. Takes about a minute.
              </p>
              <button className="primary-button large" disabled={Boolean(busy)} onClick={() => generate(false)}>
                {busy === "generate" ? "Tailoring resume…" : "Generate tailored resume & cover letter"}
              </button>
              <p className="or-divider">or skip generating</p>
              <div className="agent-actions centred">
                <button className="secondary-button" disabled={Boolean(busy)} onClick={openLibrary}>
                  <FileText size={15} /> {busy === "library" ? "Loading…" : "Use a saved resume"}
                </button>
                <label className="secondary-button upload-inline">
                  <Upload size={15} /> {busy === "upload" ? "Uploading…" : "Upload a resume & apply"}
                  <input
                    ref={resumeInput}
                    type="file"
                    accept=".pdf,.docx,.txt,.md"
                    disabled={Boolean(busy)}
                    onChange={(e) => uploadAndAttach(e.target.files?.[0])}
                  />
                </label>
              </div>
              {library && <LibraryPicker items={library} busy={busy} onPick={attachResume} onCancel={() => setLibrary(null)} />}
            </div>
          ) : (
            <>
              {tailored.from_master && (
                <div className="master-banner">
                  <BookMarked size={16} />
                  <span>
                    <strong>
                      Tailored from &ldquo;{tailored.from_master}&rdquo; —{" "}
                      {tailored.lines_kept} of {tailored.lines_total} items kept, on{" "}
                      {tailored.pages} page{tailored.pages === 1 ? "" : "s"}.
                    </strong>
                    Selected and re-worded for this posting in your own layout and fonts.
                    Employers, titles and dates are unchanged.
                    {tailored.notes && ` ${tailored.notes}`}
                  </span>
                </div>
              )}

              <div className="score-block">
                <div className={`score-dial ${scoreClass(pkg.ats_score)}`}>
                  <strong>{Math.round(pkg.ats_score)}</strong>
                  <span>Job match</span>
                </div>
                <div className="score-breakdown">
                  {components.map(([key, part]) => (
                    <Bar
                      key={key}
                      label={part.label}
                      value={part.score}
                      weight={part.applicable ? `${part.weight}%` : "not stated"}
                      muted={!part.applicable}
                    />
                  ))}
                  <p className="hint">
                    {pkg.passes_used} tailoring pass{pkg.passes_used === 1 ? "" : "es"} ·{" "}
                    {pkg.model_used} · {report.format?.word_count ?? 0} words ·{" "}
                    {report.format?.page_count ?? 0} page(s)
                  </p>
                </div>
              </div>

              <p className="hint score-caveat">
                This is how well your resume lines up with this posting — not a score
                the employer sees. There is no standard ATS cutoff, so treat it as an
                editing aid. Parsing and eligibility are checked separately below, on
                purpose: a good match here cannot cover up a problem there.
              </p>

              <div className="gate-row">
                <Gate
                  label="Parses cleanly"
                  value={report.format?.score ?? 0}
                  detail="Can software read your experience, education and skills?"
                />
                <Gate
                  label="Reads well"
                  value={report.content?.score ?? 0}
                  detail={`${report.content?.quantified ?? 0}/${report.content?.bullet_count ?? 0} bullets carry a number.`}
                />
                <Gate
                  label="Eligibility"
                  value={eligibility.blocking?.length ? 0 : eligibility.needs_you?.length ? 55 : 100}
                  detail={
                    eligibility.blocking?.length
                      ? `${eligibility.blocking.length} condition you do not meet`
                      : eligibility.needs_you?.length
                        ? `${eligibility.needs_you.length} only you can confirm`
                        : eligibility.conditions?.length
                          ? "All stated conditions met"
                          : "None stated in this posting"
                  }
                />
              </div>

              {eligibility.conditions?.length > 0 && (
                <section className="review-section">
                  <h3>Eligibility conditions</h3>
                  <p className="section-note">
                    Pass/fail requirements from the posting. These are never folded into
                    the match score — one unmet condition can end an application that
                    scores 95.
                  </p>
                  <ul className="eligibility-list">
                    {eligibility.conditions.map((c) => (
                      <li key={c.kind} className={`eligibility-${c.status}`}>
                        <strong>{c.kind}</strong> — {c.requirement}
                        {c.note && <em> ({c.note})</em>}
                      </li>
                    ))}
                  </ul>
                </section>
              )}

              {uncovered.length > 0 && (
                <section className="review-section">
                  <h3>Responsibilities with no evidence on your resume</h3>
                  <p className="section-note">
                    Matched on words, not meaning — if you have done one of these in
                    different words, re-word the bullet that describes it.
                  </p>
                  <ul className="plain-list">
                    {uncovered.slice(0, 8).map((item) => (
                      <li key={item.responsibility}>{item.responsibility}</li>
                    ))}
                  </ul>
                </section>
              )}

              {report.issues?.length > 0 && (
                <div className="review-warning">
                  <strong>Score notes</strong>
                  <ul>{report.issues.map((issue) => <li key={issue}>{issue}</li>)}</ul>
                </div>
              )}

              <section className="review-section">
                <h3>Documents</h3>
                <div className="agent-actions">
                  <a className="secondary-button" href={resumeUrl(pkg.application_id)} target="_blank" rel="noreferrer">
                    <FileText size={15} /> Tailored resume
                  </a>
                  {pkg.cover_letter_path && (
                    <a className="secondary-button" href={coverLetterUrl(pkg.application_id)} target="_blank" rel="noreferrer">
                      <FileText size={15} /> Cover letter
                    </a>
                  )}
                  <button className="table-button" disabled={busy === "generate"} onClick={() => generate(true)}>
                    {busy === "generate" ? "Regenerating…" : "Regenerate"}
                  </button>
                  <button className="table-button" disabled={Boolean(busy)} onClick={openLibrary}>
                    {busy === "library" ? "Loading…" : "Use a saved resume"}
                  </button>
                  <label className="table-button upload-inline">
                    <Upload size={14} /> {busy === "upload" ? "Uploading…" : "Upload & use"}
                    <input
                      type="file"
                      accept=".pdf,.docx,.txt,.md"
                      disabled={Boolean(busy)}
                      onChange={(e) => uploadAndAttach(e.target.files?.[0])}
                    />
                  </label>
                </div>

                {library && (
                  <LibraryPicker
                    items={library}
                    busy={busy}
                    onPick={attachResume}
                    onCancel={() => setLibrary(null)}
                  />
                )}
                <p className="hint">Headline: <strong>{pkg.headline}</strong></p>
                <p className="summary-preview">{pkg.summary}</p>
              </section>

              {tailored.gaps?.length > 0 && (
                <section className="review-section">
                  <h3>Gaps this resume cannot close</h3>
                  <p className="section-note">
                    Requirements no wording can satisfy. A missing keyword is fixable;
                    a missing qualification is not, and claiming it is what gets caught
                    in an interview.
                  </p>
                  <ul className="plain-list">
                    {tailored.gaps.map((gap) => <li key={gap}>{gap}</li>)}
                  </ul>
                </section>
              )}

              <section className="review-section">
                <h3>Keyword match</h3>
                <div className="chip-row">
                  {matched.slice(0, 30).map((term) => (
                    <span className="chip chip-ok" key={term}>{term}</span>
                  ))}
                </div>
                {adopted.length > 0 && (
                  <>
                    <p className="hint">
                      Added to your skills list and saved to your profile, so every
                      later application has them too. Anything here you have not
                      actually used, delete in Profile → Skills.
                    </p>
                    <div className="chip-row">
                      {adopted.map((term) => (
                        <span className="chip chip-added" key={term}>
                          {term} <em>added</em>
                        </span>
                      ))}
                    </div>
                  </>
                )}

                {missing.length > 0 && (
                  <>
                    <p className="hint">
                      Still not on your resume. These are either not skills, or nothing
                      in your profile shows the work behind them. Add the real experience
                      to your profile and regenerate.
                    </p>
                    <div className="chip-row">
                      {missing.slice(0, 20).map((m) => (
                        <span className={`chip chip-${m.importance}`} key={m.term}>
                          {m.term} <em>{m.importance.replace("_", " ")}</em>
                        </span>
                      ))}
                    </div>
                  </>
                )}
              </section>

              <section className="review-section">
                <h3>
                  <MessageSquare size={16} /> Employer-specific questions
                </h3>
                <p>
                  Questions unique to this application — &ldquo;Why this company?&rdquo; and the
                  like. Claude drafts them from your profile and this job description; anything
                  only you can answer is flagged. Edit freely, then save.
                </p>
                <div className="agent-actions">
                  <button className="secondary-button" disabled={busy === "draft"} onClick={draft}>
                    {busy === "draft" ? "Reading the form…" : drafts ? "Re-draft" : "Draft answers"}
                  </button>
                  {drafts?.length > 0 && (
                    <button className="primary-button" disabled={busy === "saveanswers"} onClick={storeAnswers}>
                      {busy === "saveanswers" ? "Saving…" : "Save answers"}
                    </button>
                  )}
                </div>

                {drafts?.length === 0 && (
                  <p className="hint">No extra questions found beyond what your profile covers.</p>
                )}
                {drafts?.map((item, index) => (
                  <div className={`draft-item ${item.needs_you ? "needs-you" : ""}`} key={item.field_name}>
                    <label htmlFor={`draft-${item.field_name}`}>
                      {item.question}
                      {item.legal ? <em className="needs-chip legal-chip">legal agreement — read it first</em>
                        : item.needs_you ? <em className="needs-chip">only you can answer this</em> : null}
                    </label>
                    <small>{item.reason}</small>
                    {item.options?.length ? (
                      <select
                        id={`draft-${item.field_name}`}
                        value={item.answer || ""}
                        onChange={(e) =>
                          setDrafts((list) =>
                            list.map((d, i) => (i === index ? { ...d, answer: e.target.value } : d)),
                          )
                        }
                      >
                        <option value="">Leave for me to answer on the form</option>
                        {item.options.map((option) => (
                          <option key={option} value={option}>{option}</option>
                        ))}
                      </select>
                    ) : (
                      <textarea
                        id={`draft-${item.field_name}`}
                        rows={item.answer && item.answer.length > 120 ? 5 : 2}
                        placeholder={item.needs_you ? "Type your answer…" : ""}
                        value={item.answer || ""}
                        onChange={(e) =>
                          setDrafts((list) =>
                            list.map((d, i) => (i === index ? { ...d, answer: e.target.value } : d)),
                          )
                        }
                      />
                    )}
                  </div>
                ))}
              </section>

              <section className="review-section">
                <h3>
                  <MousePointerClick size={16} /> Fill the application
                </h3>
                <p>
                  Opens the employer&apos;s form in your browser and fills every field it can
                  identify, attaching the documents above. It never clicks Submit, never
                  accepts an agreement, and never answers a demographic question.
                </p>
                <div className="agent-actions">
                  <button className="primary-button" disabled={busy === "autofill"} onClick={fillForm}>
                    {busy === "autofill" ? "Opening browser…" : "Open & fill form"}
                  </button>
                  <button className="secondary-button" disabled={Boolean(busy)} onClick={fillCurrent}>
                    {busy === "fillcurrent" ? "Filling…" : "Fill the page I'm on"}
                  </button>
                  <a className="secondary-button" href={job.application_url} target="_blank" rel="noreferrer">
                    Open job page <ExternalLink size={14} />
                  </a>
                </div>
                <p className="hint">
                  Behind a sign-in (Workday, Taleo) or split across steps? Sign in yourself in
                  the JobApplyAI browser, reach any step of the form, then press
                  <strong> Fill the page I&apos;m on</strong> — once per step.
                </p>

                {autofill && (
                  <div className="autofill-report">
                    <p><strong>{autofill.note}</strong></p>
                    {autofill.filled?.length > 0 && (
                      <details open>
                        <summary>Filled {autofill.filled.length} fields</summary>
                        <ul>
                          {autofill.filled.map((f, i) => (
                            <li key={i}>{f.field} → <code>{f.value}</code></li>
                          ))}
                        </ul>
                      </details>
                    )}
                    {autofill.needs_your_input?.length > 0 && (
                      <details open>
                        <summary>Needs your input ({autofill.needs_your_input.length})</summary>
                        <ul>
                          {autofill.needs_your_input.map((f, i) => (
                            <li key={i}>{typeof f === "string" ? f : `${f.field} — ${f.reason}`}</li>
                          ))}
                        </ul>
                      </details>
                    )}
                    {autofill.skipped?.length > 0 && (
                      <details>
                        <summary>Skipped {autofill.skipped.length}</summary>
                        <ul>
                          {autofill.skipped.map((f, i) => <li key={i}>{f.field} — {f.reason}</li>)}
                        </ul>
                      </details>
                    )}
                  </div>
                )}
              </section>

              <section className="review-section">
                <h3>
                  <CheckCircle2 size={16} /> After you submit
                </h3>
                <p>
                  Nothing marks an application as sent automatically. Once you have clicked
                  Submit on the employer&apos;s site, record it here so the tracker is accurate.
                </p>
                <div className="add-source-row">
                  <input
                    className="narrow"
                    placeholder="Confirmation ID (optional)"
                    value={confirmationId}
                    onChange={(e) => setConfirmationId(e.target.value)}
                  />
                  <button className="approve-button" disabled={busy === "applied"} onClick={confirmApplied}>
                    {busy === "applied" ? "Saving…" : "I submitted this application"}
                  </button>
                </div>
              </section>
            </>
          )}

          <section className="review-section">
            <h3>Job description</h3>
            {(job.description || "").trim() ? (
              <p className="description-preview">{job.description}</p>
            ) : (
              <p className="description-preview muted">
                Not captured for this posting yet — use <strong>Read it in my browser</strong>
                {" "}or paste it above. The job, its history and any documents are unaffected.
              </p>
            )}
          </section>
        </div>

        <div className="modal-actions">
          <button className="secondary-button" disabled={Boolean(busy)} onClick={onClose}>
            Close
          </button>
        </div>
      </section>
    </div>
  );
}

function LibraryPicker({ items, busy, onPick, onCancel }) {
  if (!items.length) {
    return (
      <div className="library-picker">
        <p className="hint">
          Your library is empty. Add resumes under the <strong>Resumes</strong> tab, or use
          Upload above to add one right here.
        </p>
        <button className="secondary-button" onClick={onCancel}>Close</button>
      </div>
    );
  }
  return (
    <div className="library-picker">
      <p className="hint">
        Pick one to attach instead of generating. This application keeps its own copy, so
        changing the library later will not alter what you sent.
      </p>
      {items.map((item) => (
        <button
          className="library-option"
          key={item.id}
          disabled={Boolean(busy)}
          onClick={() => onPick(item.id)}
        >
          <span className={`resume-score small ${scoreClass(item.ats_score)}`}>
            {item.ats_score == null ? "—" : Math.round(item.ats_score)}
          </span>
          <span className="library-option-main">
            <strong>{item.label}</strong>
            <em>
              {item.role_target || "No role set"} · {item.kind === "uploaded" ? "uploaded" : "built here"}
            </em>
          </span>
        </button>
      ))}
      <button className="secondary-button" onClick={onCancel}>Cancel</button>
    </div>
  );
}

function Bar({ label, value, weight, muted }) {
  return (
    <div className={`score-bar ${muted ? "score-bar-muted" : ""}`}>
      <span className="score-bar-label">
        {label} <em>{weight}</em>
      </span>
      <div className="score-track">
        <div
          className={`score-fill ${muted ? "score-none" : scoreClass(value)}`}
          style={{ width: muted ? "0%" : `${Math.max(2, Math.min(100, value))}%` }}
        />
      </div>
      <span className="score-value">{muted ? "—" : Math.round(value)}</span>
    </div>
  );
}

/** A pass/fail check shown next to the match score, never inside it. */
function Gate({ label, value, detail }) {
  return (
    <div className={`gate ${scoreClass(value)}`}>
      <span className="gate-label">{label}</span>
      <strong>{Math.round(value)}</strong>
      <small>{detail}</small>
    </div>
  );
}
