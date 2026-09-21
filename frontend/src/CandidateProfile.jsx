import { useEffect, useRef, useState } from "react";
import { Upload, Save } from "lucide-react";

import { getProfile, importResume, requestMessage, saveProfile } from "./api";
import { pendingSkills, reviewSkill } from "./profileReview";

const TEXT_FIELDS = [
  ["first_name", "First name"],
  ["last_name", "Last name"],
  ["email", "Email"],
  ["phone", "Phone (with country code)"],
  ["location", "Location shown on your resume"],
  ["website", "Website / portfolio"],
  ["github", "GitHub URL"],
  ["linkedin", "LinkedIn"],
  ["publications_url", "Google Scholar / publications"],
  ["earliest_start", "Earliest start date"],
];

// Workday's own wording, which most other trackers copy.
const PHONE_DEVICE_TYPES = ["Mobile", "Home", "Work", "Fax", "Pager"];

const AUTH_FIELDS = [
  ["authorized_to_work", "Authorized to work in this country?"],
  ["requires_sponsorship_now", "Require sponsorship now?"],
  ["requires_sponsorship_future", "Require sponsorship now or in future?"],
  ["relocation", "Open to relocation?"],
  ["office_25_percent", "Open to office 25% of the time?"],
];

const ADDRESS_FIELDS = [
  ["address_line1", "Address line 1"],
  ["address_line2", "Address line 2 (apt, suite)"],
  ["city", "City"],
  ["state", "State / province"],
  ["postal_code", "ZIP / postal code"],
  ["country", "Country"],
];

const SECTIONS = ["experience", "projects", "education", "publications"];

const SELF_ID_FIELDS = [
  ["gender", "Gender", ["Male", "Female", "Decline To Self Identify"]],
  ["hispanic_latino", "Hispanic / Latino?", ["Yes", "No", "Decline To Self Identify"]],
  ["race_ethnicity", "Race / ethnicity", [
    "American Indian or Alaska Native",
    "Asian",
    "Black or African American",
    "Hispanic or Latino",
    "Native Hawaiian or Other Pacific Islander",
    "White",
    "Two or More Races",
    "Decline To Self Identify",
  ]],
  ["veteran_status", "Veteran status", [
    "I am not a protected veteran",
    "I identify as one or more of the classifications of a protected veteran",
    "I don't wish to answer",
  ]],
  ["disability_status", "Disability status", [
    "Yes, I have a disability, or have had one in the past",
    "No, I do not have a disability and have not had one in the past",
    "I do not want to answer",
  ]],
];

const EMPTY_ENTRY = {
  heading: "", organization: "", location: "", bullets: [], dates: "",
  start_month: "", start_year: "", end_month: "", end_year: "", current: false,
  degree: "", major: "", gpa: "",
};

// Education is asked for as separate fields on real applications, so it is kept
// that way here rather than split out of one combined heading later.
const MONTHS = [
  ["", "Month"], ["01", "Jan"], ["02", "Feb"], ["03", "Mar"], ["04", "Apr"],
  ["05", "May"], ["06", "Jun"], ["07", "Jul"], ["08", "Aug"], ["09", "Sep"],
  ["10", "Oct"], ["11", "Nov"], ["12", "Dec"],
];

// Each section asks for exactly what that kind of entry needs - projects have no
// employer, education has years rather than months.
const ENTRY_FIELDS = {
  education: [
    ["degree", "Degree (e.g. M.S., B.Tech.)", "text"],
    ["major", "Major / field of study", "text"],
    ["organization", "School or university", "text"],
    ["gpa", "GPA (optional)", "text"],
    ["start_year", "Start year", "year"],
    ["end_year", "End year (or expected)", "year"],
    ["location", "Location", "text"],
  ],
  projects: [
    ["heading", "Title", "text"],
    ["start_year", "Year", "year"],
  ],
  publications: [
    ["heading", "Title", "text"],
    ["organization", "Venue / publisher", "text"],
    ["start_year", "Year", "year"],
  ],
  default: [
    ["heading", "Role / title", "text"],
    ["organization", "Employer / organisation", "text"],
    ["location", "Location", "text"],
    ["start_month", "Start", "monthyear"],
    ["end_month", "End", "monthyear"],
  ],
};

export default function CandidateProfile() {
  const [profile, setProfile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [importReview, setImportReview] = useState(null);
  const fileInput = useRef(null);

  useEffect(() => {
    getProfile().then(setProfile).catch((e) => setError(requestMessage(e)));
  }, []);

  function change(key, value) {
    setProfile((p) => ({ ...p, data: { ...p.data, [key]: value } }));
    setMessage("Unsaved changes");
  }

  async function save(event) {
    event?.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    try {
      setProfile(await saveProfile(profile));
      setMessage("Profile saved. Regenerate any existing applications to use the changes.");
    } catch (e) {
      setError(requestMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function upload(file) {
    if (!file) return;
    setBusy(true);
    setError("");
    setImportReview(null);
    setMessage("Reading your resume with Claude…");
    try {
      // Include unsaved edits in the merge. The server returns a preview, and
      // the form stays disabled until it is ready; saving remains explicit.
      const result = await importResume(file, profile);
      setProfile({ revision: result.current_revision, data: result.merged_preview });
      setImportReview(result);
      const counts = SECTIONS.map(
        (s) => `${(result.parsed[s] || []).length} ${s}`,
      ).join(", ");
      setMessage(`Imported ${counts}, ${(result.parsed.skills || []).length} skills. Review below, then Save profile.`);
    } catch (e) {
      setError(requestMessage(e));
      setMessage("");
    } finally {
      setBusy(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  if (!profile) return (
    <div className="empty-state" role={error ? "alert" : "status"}>
      {error || "Loading profile…"}
    </div>
  );

  const data = profile.data;
  const pending = pendingSkills(data);
  const thin = !(data.experience?.length || data.projects?.length);

  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <h3>Candidate profile</h3>
        </div>
        <div className="header-actions">
          <label className="secondary-button upload-inline">
            <Upload size={15} /> {busy ? "Working…" : "Import from resume"}
            <input
              ref={fileInput}
              type="file"
              accept=".pdf,.docx,.txt,.md"
              disabled={busy}
              onChange={(e) => upload(e.target.files?.[0])}
            />
          </label>
          <button className="primary-button" disabled={busy} onClick={save}>
            <Save size={15} /> {busy ? "Saving…" : "Save profile"}
          </button>
        </div>
      </div>

      {error && <div className="alert error-alert">{error}</div>}
      {message && <div className="alert approval-alert">{message}</div>}
      {importReview?.coverage_warning && (
        <div className="alert warn-alert">{importReview.coverage_warning}</div>
      )}
      {importReview?.conflicts?.length > 0 && (
        <details className="review-section">
          <summary>{importReview.conflicts.length} imported values differ. Your existing values were kept.</summary>
          <p className="section-note">Review these differences and edit the fields below if needed.</p>
          <ul>
            {importReview.conflicts.map((conflict, index) => (
              <li key={index}>
                <strong>{conflict.entry || conflict.section}: {conflict.field.replaceAll("_", " ")}</strong>
                {` — kept "${conflict.yours}"; document says "${conflict.from_document}".`}
              </li>
            ))}
          </ul>
        </details>
      )}
      {thin && (
        <div className="alert warn-alert">
          <span>
            No experience or projects yet — resume generation is blocked until you add at
            least one. Fastest path: <strong>Import from resume</strong> above.
          </span>
        </div>
      )}

      <form onSubmit={save} className="profile-form">
        <fieldset disabled={busy}>
          <section className="review-section">
            <h3>Contact &amp; links</h3>
            <div className="candidate-grid">
              {TEXT_FIELDS.map(([key, label]) => (
                <label key={key}>
                  {label}
                  <input
                    type={key === "email" ? "email" : "text"}
                    value={data[key] || ""}
                    onChange={(e) => change(key, e.target.value)}
                  />
                </label>
              ))}
              <label>
                Phone device type
                <select
                  value={data.phone_device_type || "Mobile"}
                  onChange={(e) => change("phone_device_type", e.target.value)}
                >
                  {PHONE_DEVICE_TYPES.map((option) => (
                    <option key={option} value={option}>{option}</option>
                  ))}
                </select>
              </label>
            </div>
          </section>

          <section className="review-section">
            <h3>Mailing address</h3>
            <div className="candidate-grid">
              {ADDRESS_FIELDS.map(([key, label]) => (
                <label key={key}>
                  {label}
                  <input value={data[key] || ""} onChange={(e) => change(key, e.target.value)} />
                </label>
              ))}
            </div>
          </section>

          <section className="review-section">
            <h3>Summary &amp; skills</h3>
            <label className="profile-field">
              Professional summary
              <textarea rows={3} value={data.summary || ""} onChange={(e) => change("summary", e.target.value)} />
            </label>
            <label className="profile-field">
              Skills (one per line)
              <textarea
                rows={8}
                value={(data.skills || []).join("\n")}
                onChange={(e) => change("skills", e.target.value.split("\n"))}
              />
            </label>

            {pending.length > 0 && (
              <div className="adopted-skills">
                <p className="section-note">
                  These skills were added automatically and need your review. They are
                  excluded from new profile-based resumes and autofill until confirmed.
                  Confirm skills you have used, or remove them, then Save profile.
                </p>
                <div className="chip-row">
                  {pending.map((term) => (
                    <span className="chip chip-added" key={term}>
                      {term}
                      <button
                        type="button"
                        className="secondary-button"
                        aria-label={`Confirm you have used ${term}`}
                        onClick={() => {
                          setProfile((p) => ({ ...p, data: reviewSkill(p.data, term, true) }));
                          setMessage(`Confirmed ${term} in this draft. Save profile to keep the change.`);
                        }}
                      >
                        I have used this
                      </button>
                      <button
                        type="button"
                        className="chip-remove"
                        aria-label={`Remove ${term}`}
                        onClick={() => {
                          setProfile((p) => ({ ...p, data: reviewSkill(p.data, term, false) }));
                          setMessage(`Removed ${term} from this draft. Save profile to keep the change.`);
                        }}
                      >
                        ×
                      </button>
                    </span>
                  ))}
                </div>
              </div>
            )}
          </section>

          {SECTIONS.map((section) => (
            <section className="review-section" key={section}>
              <h3>{section[0].toUpperCase() + section.slice(1)}</h3>
              {(data[section] || []).map((entry, index) => {
                const edit = (key, value) =>
                  change(section, data[section].map((row, i) => (i === index ? { ...row, [key]: value } : row)));
                return (
                  <div className="history-entry" key={index}>
                    <div className="history-entry-head">
                      <span>{section.replace(/s$/, "")} {index + 1}</span>
                      <button
                        type="button"
                        className="cancel-button"
                        onClick={() => change(section, data[section].filter((_, i) => i !== index))}
                      >
                        Remove
                      </button>
                    </div>
                    <div className="candidate-grid">
                      {(ENTRY_FIELDS[section] || ENTRY_FIELDS.default).map(([key, label, kind]) => {
                        const isEnd = key.startsWith("end_");
                        if (kind === "monthyear") {
                          const yearKey = isEnd ? "end_year" : "start_year";
                          return (
                            <label key={key}>
                              {label}
                              <span className="date-parts">
                                <select
                                  value={entry[key] || ""}
                                  disabled={isEnd && entry.current}
                                  onChange={(e) => edit(key, e.target.value)}
                                >
                                  {MONTHS.map(([value, text]) => (
                                    <option key={value} value={value}>{text}</option>
                                  ))}
                                </select>
                                <input
                                  type="text"
                                  inputMode="numeric"
                                  placeholder="Year"
                                  maxLength={4}
                                  disabled={isEnd && entry.current}
                                  value={entry[yearKey] || ""}
                                  onChange={(e) => edit(yearKey, e.target.value.replace(/\D/g, ""))}
                                />
                              </span>
                              {isEnd && (
                                <label className="check-inline">
                                  <input
                                    type="checkbox"
                                    checked={Boolean(entry.current)}
                                    onChange={(e) => edit("current", e.target.checked)}
                                  />
                                  I currently work here
                                </label>
                              )}
                            </label>
                          );
                        }
                        if (kind === "year") {
                          return (
                            <label key={key}>
                              {label}
                              <input
                                type="text"
                                inputMode="numeric"
                                placeholder="YYYY"
                                maxLength={4}
                                value={entry[key] || ""}
                                onChange={(e) => edit(key, e.target.value.replace(/\D/g, ""))}
                              />
                            </label>
                          );
                        }
                        return (
                          <label key={key}>
                            {label}
                            <input value={entry[key] || ""} onChange={(e) => edit(key, e.target.value)} />
                          </label>
                        );
                      })}
                    </div>
                    <label className="profile-field">
                      {section === "education" ? "Coursework / notes" : "Description"}
                      <textarea
                        rows={5}
                        value={(entry.bullets || []).join("\n")}
                        onChange={(e) => edit("bullets", e.target.value.split("\n"))}
                      />
                    </label>
                  </div>
                );
              })}
              <button
                type="button"
                className="secondary-button"
                onClick={() => change(section, [...(data[section] || []), { ...EMPTY_ENTRY }])}
              >
                Add {section} entry
              </button>
            </section>
          ))}

          <section className="review-section">
            <h3>Work authorization &amp; preferences</h3>
            <p className="hint">
              These are your own confirmed answers, not a legal determination. They are used
              to fill application questions.
            </p>
            <div className="candidate-grid">
              <label>
                Work authorization country (full name)
                <input value={data.work_country || ""} onChange={(e) => change("work_country", e.target.value)} />
              </label>
              {AUTH_FIELDS.map(([key, label]) => (
                <label key={key}>
                  {label}
                  <select
                    value={data[key] == null ? "" : String(data[key])}
                    onChange={(e) => change(key, e.target.value === "" ? null : e.target.value === "true")}
                  >
                    <option value="">Not confirmed — ask me</option>
                    <option value="true">Yes</option>
                    <option value="false">No</option>
                  </select>
                </label>
              ))}
            </div>
          </section>

          <section className="review-section">
            <h3>Voluntary self-identification</h3>
            <p className="hint">
              US employers ask these for EEO reporting; they are optional and are not shown
              to hiring managers. Whatever you pick here is copied onto forms exactly as
              written — nothing is ever inferred. Leave any blank to keep answering it
              yourself. &ldquo;Decline to self-identify&rdquo; is a real answer and will be
              filled like any other.
            </p>
            <div className="candidate-grid">
              {SELF_ID_FIELDS.map(([key, label, options]) => (
                <label key={key}>
                  {label}
                  <select value={data[key] || ""} onChange={(e) => change(key, e.target.value)}>
                    <option value="">Leave blank — I&apos;ll answer manually</option>
                    {options.map((option) => (
                      <option key={option} value={option}>{option}</option>
                    ))}
                  </select>
                </label>
              ))}
            </div>
          </section>

          <button className="primary-button" type="submit" disabled={busy}>
            <Save size={15} /> {busy ? "Saving…" : "Save profile"}
          </button>
        </fieldset>
      </form>
    </section>
  );
}
