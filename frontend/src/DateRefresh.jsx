import { useState } from "react";
import { refreshJobDates, requestMessage } from "./api";

export default function DateRefresh({ jobs, disabled, onUpdated }) {
  const [busy, setBusy] = useState(false);
  const [attempted, setAttempted] = useState([]);
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");
  const missing = jobs.filter((job) => !job.posted_at);
  const pending = missing.filter((job) => !attempted.includes(job.id));

  async function refresh() {
    setBusy(true);
    setError("");
    try {
      const result = await refreshJobDates(pending.slice(0, 10).map((job) => job.id));
      setAttempted((old) => [...new Set([...old, ...result.results.map((item) => item.id)])]);
      setReport(result);
      await onUpdated();
    } catch (e) {
      setError(requestMessage(e));
    } finally {
      setBusy(false);
    }
  }

  if (!missing.length && !report && !busy) return null;
  return (
    <div style={{ padding: "12px 20px", borderBottom: "1px solid #e2e8f0" }}>
      <button className="table-button" disabled={disabled || busy || !pending.length} onClick={refresh}>
        {busy ? "Reading posting dates…" : `Refresh missing dates (${Math.min(10, pending.length)})`}
      </button>
      {attempted.length > 0 && !busy && (
        <button className="table-button" disabled={disabled} style={{ marginLeft: 8 }} onClick={() => { setAttempted([]); setReport(null); }}>
          Retry unavailable dates
        </button>
      )}
      <p style={{ margin: "8px 0" }}>
        {jobs.length - missing.length} of {jobs.length} jobs have a posting date.
        {busy ? " Keep the app open while it reads up to 10 saved job links." : ` ${pending.length} missing dates still to check in this session.`}
        {" Dates such as “3 days ago” are estimates. Some sites do not provide a date."}
      </p>
      {error && <p role="alert">{error}</p>}
      {report && (
        <details open={report.results.some((item) => item.status === "blocked" || item.status === "error")}>
          <summary role="status">Last batch: {report.checked} checked, {report.recovered} dates recovered.</summary>
          <ul>
            {report.results.map((item) => (
              <li key={item.id}>
                <strong>{item.title || "Job"}:</strong> {item.message}
                {item.evidence && ` (${item.evidence}${item.estimated ? "; estimated" : ""})`}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
