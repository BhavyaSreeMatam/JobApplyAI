import axios from "axios";

const API_BASE_URL = (import.meta.env?.VITE_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: { "Content-Type": "application/json" },
  timeout: 900000, // tailoring a resume is several model calls
});

export function requestMessage(error) {
  const detail = error?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => `${item.loc?.slice(1).join(".") || "Input"}: ${item.msg}`)
      .join("; ");
  }
  if (error?.code === "ECONNABORTED") return "The request timed out. Check the backend terminal.";
  if (error?.code !== "ERR_NETWORK" && error?.message) return error.message;
  return "Request failed. Check that the backend is running on port 8000.";
}

const unwrap = (promise) => promise.then((response) => response.data);

/* ---------------------------------- health --------------------------------- */
export const getHealth = () => unwrap(api.get("/health"));

/* --------------------------------- settings -------------------------------- */
export const getSettings = () => unwrap(api.get("/config"));
export const saveSettings = (body) => unwrap(api.put("/config", body));
export const testApiKey = () => unwrap(api.post("/config/test-key"));

/* --------------------------------- profile --------------------------------- */
export const getProfile = () => unwrap(api.get("/agent/profile"));
export const saveProfile = (body) => unwrap(api.put("/agent/profile", body));
export const importResume = (file, profile) => {
  const form = new FormData();
  form.append("resume", file);
  if (profile) {
    form.append("profile_json", JSON.stringify(profile.data));
    form.append("revision", String(profile.revision));
  }
  return unwrap(
    api.post("/config/import-resume", form, {
      headers: { "Content-Type": "multipart/form-data" },
    }),
  );
};

/* --------------------------------- sources --------------------------------- */
export const getSources = () => unwrap(api.get("/sources"));
export const addCareersPage = (url, label) =>
  unwrap(api.post("/sources/careers-page", { url, label }));
export const addBrowserSource = (body) => unwrap(api.post("/sources/browser", body));
export const updateSource = (id, body) => unwrap(api.patch(`/sources/${id}`, body));
export const deleteSource = (id) => api.delete(`/sources/${id}`);
export const syncSource = (id) => unwrap(api.post(`/sources/${id}/sync`));
export const syncAllSources = (boardsOnly) =>
  unwrap(api.post(`/sources/sync?boards_only=${boardsOnly ? "true" : "false"}`));
export const openBrowser = (platform) =>
  unwrap(api.post(`/sources/browser/open?platform=${encodeURIComponent(platform || "")}`));
export const closeBrowser = () => unwrap(api.post("/sources/browser/close"));

/* ----------------------------------- jobs ---------------------------------- */
export const getJobs = () => unwrap(api.get("/jobs"));
export const refreshJobDates = (jobIds) => unwrap(api.post("/jobs/dates/refresh", { job_ids: jobIds }));
export const getRecommendedJobs = () => unwrap(api.get("/jobs/recommended?minimum_score=55&limit=50"));
export const getApplications = () => unwrap(api.get("/applications"));
export const scoreJobs = (body) => unwrap(api.post("/matching/score", body));

/* ---------------------------------- apply ---------------------------------- */
export const prepareApplication = (jobId, regenerate = false) =>
  unwrap(api.post("/apply/prepare", { job_id: jobId, regenerate }));
export const getPackage = (applicationId) => unwrap(api.get(`/apply/${applicationId}`));
export const runAutofill = (applicationId) => unwrap(api.post(`/apply/${applicationId}/autofill`));
export const markApplied = (applicationId, confirmationId = "") =>
  unwrap(api.post(`/apply/${applicationId}/mark-applied`, { confirmation_id: confirmationId }));
export const resetApplication = (applicationId) => unwrap(api.post(`/apply/${applicationId}/reset`));

export const resumeUrl = (applicationId) => `${API_BASE_URL}/apply/${applicationId}/resume`;
export const coverLetterUrl = (applicationId) => `${API_BASE_URL}/apply/${applicationId}/cover-letter`;
export const excelDownloadUrl = `${API_BASE_URL}/exports/applications/excel`;

export const draftAnswers = (applicationId) =>
  unwrap(api.post(`/apply/${applicationId}/draft-answers`));
export const saveAnswers = (applicationId, answers) =>
  unwrap(api.post(`/apply/${applicationId}/answers`, { answers }));

export const addSingleJob = (url) => unwrap(api.post("/sources/single-job", { url }));
export const getJob = (jobId) => unwrap(api.get(`/jobs/${jobId}`));

/* --------------------------------- resumes -------------------------------- */
export const getResumes = () => unwrap(api.get("/resumes"));
export const buildResume = (body) => unwrap(api.post("/resumes/build", body));
export const scoreResume = (id, body) => unwrap(api.post(`/resumes/${id}/score`, body));
export const updateResume = (id, body) => unwrap(api.patch(`/resumes/${id}`, body));
export const setMasterResume = (id) => unwrap(api.post(`/resumes/${id}/master`));
export const fetchJobDescription = (id) => unwrap(api.post(`/jobs/${id}/fetch-description`));
export const setJobDescription = (id, description) =>
  unwrap(api.post(`/jobs/${id}/description`, { description }));
export const getMasterPreview = () => unwrap(api.get("/resumes/master/preview"));
export const deleteResume = (id) => api.delete(`/resumes/${id}`);
export const uploadResumeToLibrary = (file, label, roleTarget) => {
  const form = new FormData();
  form.append("resume", file);
  form.append("label", label || "");
  form.append("role_target", roleTarget || "");
  return unwrap(
    api.post("/resumes/upload", form, { headers: { "Content-Type": "multipart/form-data" } }),
  );
};
export const resumeFileUrl = (id) => `${API_BASE_URL}/resumes/${id}/download`;
export const attachLibraryResume = (jobId, resumeId) =>
  unwrap(api.post("/apply/use-resume", { job_id: jobId, resume_id: resumeId }));

export const getJobSourceSummary = () => unwrap(api.get("/jobs/sources/summary"));
export const bulkDeleteJobs = (body) => unwrap(api.post("/jobs/bulk-delete", body));

export const clearApplications = () => unwrap(api.post("/applications/clear"));

export const autofillCurrentPage = (applicationId) =>
  unwrap(api.post(`/apply/${applicationId}/autofill-current`));
