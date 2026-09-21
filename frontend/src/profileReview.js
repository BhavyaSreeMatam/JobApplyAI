// Keep these rules aligned with backend/app/services/profile_merge.py.
export const skillKey = (value) => String(value || "").toLowerCase().trim().replace(/\s+/g, " ");

function skillSource(marks, skill) {
  const key = skillKey(skill);
  const legacyKey = key.replace(/[^a-z0-9]+/g, " ").trim();
  return marks[key] ?? (marks[legacyKey] === "machine" ? "machine" : undefined);
}

export function pendingSkills(data) {
  const marks = data.provenance?.skills || {};
  const adopted = new Set((data.adopted_skills || []).map(skillKey));
  return [...new Set((data.skills || []).filter((skill) => {
    const key = skillKey(skill);
    const source = skillSource(marks, skill);
    return key && (source === "machine" || (source == null && adopted.has(key)));
  }))];
}

export function reviewSkill(data, skill, confirmed) {
  const key = skillKey(skill);
  const marks = { ...data.provenance?.skills };
  for (const existing of data.skills || []) {
    const source = skillSource(data.provenance?.skills || {}, existing);
    if (source != null) marks[skillKey(existing)] = source;
  }
  if (confirmed) marks[key] = "user";
  // Keep a machine mark after removal: re-importing a document can promote it,
  // but simply deleting a line must not approve any similarly named skill.
  return {
    ...data,
    skills: confirmed ? data.skills : (data.skills || []).filter((s) => skillKey(s) !== key),
    adopted_skills: (data.adopted_skills || []).filter((s) => skillKey(s) !== key),
    provenance: { ...data.provenance, skills: marks },
  };
}
