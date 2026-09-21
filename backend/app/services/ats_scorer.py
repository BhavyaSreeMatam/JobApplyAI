"""Deterministic measurements taken from resume text.

Three of the four reports the app shows live here - keyword coverage, parsing
compatibility, and resume quality - along with the tokenising and phrase-matching
primitives they share. No LLM is involved, so every number is reproducible and
can serve as an objective target for the tailoring loop.

These are measurements, not a verdict. app.services.job_match puts them beside
the job-match score and the eligibility check without averaging them together;
see that module for why.
"""
import re
from pathlib import Path

STOPWORDS = {
    "a", "the", "and", "or", "for", "with", "you", "your", "our", "we", "us", "to", "of", "in",
    "on", "at", "by", "as", "is", "are", "be", "will", "have", "has", "this", "that", "from", "an",
    "it", "its", "their", "they", "who", "what", "which", "can", "may", "must", "should", "would",
    "all", "any", "more", "most", "other", "such", "not", "than", "then", "so", "if", "into", "up",
    "out", "about", "over", "also", "new", "work", "working", "role", "team", "teams", "job", "join",
    "help", "make", "like", "well", "years", "year", "experience", "strong", "ability", "including",
    "etc", "across", "within", "using", "use", "used", "looking", "candidate", "candidates",
    "applicant", "please", "apply", "position", "opportunity",
}

SECTION_HEADINGS = {
    "experience", "work experience", "professional experience", "employment",
    "education", "skills", "technical skills", "projects", "summary",
    "professional summary", "publications", "certifications", "awards",
}

ACTION_VERBS = {
    "built", "designed", "led", "developed", "implemented", "created", "launched", "shipped",
    "improved", "reduced", "increased", "automated", "architected", "migrated", "optimized",
    "delivered", "owned", "scaled", "deployed", "engineered", "analyzed", "researched", "trained",
    "published", "collaborated", "mentored", "managed", "drove", "established", "integrated",
}

# Box-drawing and decorative glyphs that corrupt ATS text extraction.
UNSAFE_CHARS = set(
    "│┃║▌▐■□▪▫●◆◇★☆➤➢"
)

WEIGHTS = {"required": 3.0, "preferred": 2.0, "nice_to_have": 1.0}

BULLET_PREFIXES = ("•", "-", "–", "—")


def tokens(text):
    return re.findall(r"[a-z0-9][a-z0-9+#.\-]*", (text or "").casefold())


def content_tokens(text):
    return {t for t in tokens(text) if t not in STOPWORDS and len(t) > 1}


def extract_pdf_text(path):
    """Read the PDF back the way an ATS parser would."""
    try:
        import pdfplumber

        with pdfplumber.open(str(path)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception:
        return ""


def phrase_present(phrase, haystack):
    """Match a keyword or multi-word phrase tolerantly, but on whole words only.

    Word boundaries are asserted with lookarounds rather than \\b so terms whose
    edges are not word characters still work ("C++", ".NET", "Node.js"). Without
    this, "Go" matches the "go" inside "google" and inflates the score.
    """
    parts = [re.escape(p) for p in tokens(phrase)]
    if not parts:
        return False
    pattern = r"(?<![a-z0-9])" + r"[^a-z0-9]{0,3}".join(parts) + r"(?![a-z0-9])"
    return re.search(pattern, haystack.casefold()) is not None


def keyword_report(resume_text, keywords):
    """keywords: list of {"term": str, "importance": required|preferred|nice_to_have}."""
    matched, missing, earned, possible = [], [], 0.0, 0.0
    seen = set()
    for entry in keywords:
        term = (entry.get("term") or "").strip()
        key = term.casefold()
        if not term or key in seen:
            continue
        seen.add(key)
        importance = entry.get("importance", "preferred")
        weight = WEIGHTS.get(importance, 2.0)
        possible += weight
        if phrase_present(term, resume_text):
            matched.append(term)
            earned += weight
        else:
            missing.append({"term": term, "importance": importance})
    coverage = (earned / possible * 100) if possible else 0.0
    missing.sort(key=lambda m: WEIGHTS.get(m["importance"], 2.0), reverse=True)
    return {
        "coverage_percent": round(coverage, 1),
        "matched": matched,
        "missing": missing,
        "matched_count": len(matched),
        "total_count": len(seen),
    }


def format_report(resume_text, pdf_path=None):
    issues, checks = [], {}

    checks["text_extractable"] = len(resume_text.strip()) > 200
    if not checks["text_extractable"]:
        issues.append("PDF text could not be extracted - an ATS would read a blank resume.")

    lowered = resume_text.casefold()
    found = {
        h for h in SECTION_HEADINGS
        if re.search(r"^\s*" + re.escape(h) + r"\s*$", lowered, re.M)
    }
    checks["standard_headings"] = len(found) >= 3
    if not checks["standard_headings"]:
        issues.append(
            "Fewer than 3 standard section headings found (Experience/Education/Skills/Projects)."
        )

    checks["has_email"] = bool(re.search(r"[^\s@]+@[^\s@]+\.[a-z]{2,}", resume_text, re.I))
    if not checks["has_email"]:
        issues.append("No parseable email address.")

    checks["has_phone"] = bool(re.search(r"(\+?\d[\d\s().\-]{7,}\d)", resume_text))
    if not checks["has_phone"]:
        issues.append("No parseable phone number.")

    checks["no_unsafe_glyphs"] = not (set(resume_text) & UNSAFE_CHARS)
    if not checks["no_unsafe_glyphs"]:
        issues.append("Contains decorative glyphs that corrupt ATS parsing.")

    words = len(tokens(resume_text))
    checks["reasonable_length"] = 250 <= words <= 1100
    if words < 250:
        issues.append(f"Very short ({words} words) - likely under-detailed for keyword matching.")
    elif words > 1100:
        issues.append(f"Very long ({words} words) - trim to keep relevance density high.")

    pages = 0
    if pdf_path and Path(pdf_path).is_file():
        try:
            import pdfplumber

            with pdfplumber.open(str(pdf_path)) as pdf:
                pages = len(pdf.pages)
        except Exception:
            pages = 0
    checks["page_count_ok"] = pages in (1, 2)
    if pages > 2:
        issues.append(f"{pages} pages - most ATS-screened roles expect 1-2.")

    passed = sum(1 for value in checks.values() if value)
    return {
        "score": round(passed / len(checks) * 100, 1),
        "checks": checks,
        "issues": issues,
        "word_count": words,
        "page_count": pages,
    }


def content_report(resume_text):
    bullets = [
        line.strip().lstrip("".join(BULLET_PREFIXES)).strip()
        for line in resume_text.splitlines()
        if line.strip().startswith(BULLET_PREFIXES) and len(line.strip()) > 12
    ]
    total = len(bullets) or 1
    quantified = sum(
        1 for b in bullets
        if re.search(r"\d+\s*(%|x\b|k\b|m\b|\+|hours?|users?|ms\b)", b, re.I)
    )
    strong = sum(1 for b in bullets if tokens(b) and tokens(b)[0] in ACTION_VERBS)

    issues = []
    quant_ratio, verb_ratio = quantified / total, strong / total
    if quant_ratio < 0.3:
        issues.append(
            f"Only {quantified}/{len(bullets)} bullets contain a metric - add numbers where true."
        )
    if verb_ratio < 0.5:
        issues.append(
            f"Only {strong}/{len(bullets)} bullets open with a strong action verb."
        )
    return {
        "score": round(min(100.0, quant_ratio * 50 + verb_ratio * 50), 1),
        "bullet_count": len(bullets),
        "quantified": quantified,
        "strong_openers": strong,
        "issues": issues,
    }


# The single blended figure that used to live here - 60% keyword coverage, 25%
# parse safety, 15% content quality - has been removed on purpose. Averaging
# those three let a resume that parsed cleanly and read well hide a weak match,
# and it invited being read as an "ATS score" that employers compare against a
# threshold. No such score exists. The four measures are now reported side by
# side by app.services.job_match, which composes the reports above.
