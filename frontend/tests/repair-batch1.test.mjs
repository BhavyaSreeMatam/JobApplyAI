import assert from "node:assert/strict";
import test from "node:test";
import { pendingSkills, reviewSkill } from "../src/profileReview.js";
import { requestMessage } from "../src/api.js";

test("a missing-description Error keeps its actionable message", () => {
  const error = new Error("No description has been captured for this job yet. Paste it below.");
  assert.equal(requestMessage(error), error.message);
});

test("HTTP validation, timeout and network errors remain readable", () => {
  assert.equal(requestMessage({ response: { data: { detail: "Profile changed" } } }), "Profile changed");
  assert.match(requestMessage({ response: { data: { detail: [{ loc: ["body", "data", "skills"], msg: "Expected a list" }] } } }), /data.skills: Expected a list/);
  assert.match(requestMessage({ code: "ECONNABORTED" }), /timed out/);
  assert.match(requestMessage({ code: "ERR_NETWORK", message: "Network Error" }), /backend is running/);
  assert.match(requestMessage(null), /Request failed/);
});

test("confirming a draft skill preserves unrelated unsaved fields", () => {
  const draft = { location: "Unsaved city", relocation: false,
    skills: ["Python", "Kubernetes"], adopted_skills: ["Kubernetes"] };
  const original = structuredClone(draft);
  const confirmed = reviewSkill(draft, "Kubernetes", true);
  assert.deepEqual(pendingSkills(draft), ["Kubernetes"]);
  assert.deepEqual(pendingSkills(confirmed), []);
  assert.equal(confirmed.location, "Unsaved city");
  assert.equal(confirmed.relocation, false);
  assert.equal(confirmed.provenance.skills.kubernetes, "user");
  assert.deepEqual(draft, original);
});

test("removing a suggestion removes it from both visible and adopted skills", () => {
  const result = reviewSkill({ skills: ["Python", "Kubernetes"], adopted_skills: ["Kubernetes"] }, "kubernetes", false);
  assert.deepEqual(result.skills, ["Python"]);
  assert.deepEqual(result.adopted_skills, []);
  assert.deepEqual(pendingSkills(result), []);
});

test("document evidence overrides the legacy adopted list", () => {
  assert.deepEqual(pendingSkills({ skills: ["Python"], adopted_skills: ["Python"],
    provenance: { skills: { python: "document" } } }), []);
});

test("machine provenance without a legacy list still needs review", () => {
  assert.deepEqual(pendingSkills({ skills: ["Python"], provenance: { skills: { python: "machine" } } }), ["Python"]);
});

test("confirming C does not approve C++ or C# through an old shared key", () => {
  const result = reviewSkill({ skills: ["C", "C++", "C#"], provenance: { skills: { c: "machine" } } }, "C", true);
  assert.deepEqual(pendingSkills(result), ["C++", "C#"]);
});
