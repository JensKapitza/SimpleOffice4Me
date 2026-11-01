(() => {
  "use strict";

  const cleanText = (value) => String(value || "").trim();
  const MAX_TAGS_LENGTH = 500;

  const splitTags = (value) =>
    cleanText(value)
      .split(",")
      .map((entry) => entry.trim())
      .filter(Boolean);

  // Merge suggested tags into the existing comma-separated form value.
  // Existing text is never truncated, removed, or reordered: an
  // over-limit existing value is returned unchanged, and each new
  // deduplicated (case-insensitive) tag is appended only when the
  // whole candidate still fits within maxLength.
  const mergeTags = (existing, suggested, maxLength) => {
    const limit = Number.isFinite(maxLength) && maxLength > 0 ? Math.floor(maxLength) : MAX_TAGS_LENGTH;
    const merged = splitTags(existing);
    const base = merged.join(", ");
    if (base.length > limit) return String(existing);
    const seen = new Set(merged.map((entry) => entry.toLowerCase()));
    const candidates = Array.isArray(suggested) ? suggested : [];
    let joined = base;
    for (const candidate of candidates) {
      if (typeof candidate !== "string") continue;
      const tag = candidate.trim().replace(/\s+/g, " ");
      if (!tag || tag.includes(",") || seen.has(tag.toLowerCase())) continue;
      const next = joined ? `${joined}, ${tag}` : tag;
      if (next.length > limit) continue;
      seen.add(tag.toLowerCase());
      merged.push(tag);
      joined = next;
    }
    return joined;
  };

  // Fill the description only when the form field is still empty; an
  // existing user description is never overwritten by a suggestion note.
  const suggestionDescription = (current, note) => {
    if (cleanText(current)) return String(current);
    return cleanText(note);
  };

  const formatSuggestionTags = (suggested) => {
    const tags = (Array.isArray(suggested) ? suggested : [])
      .map((entry) => cleanText(entry))
      .filter(Boolean);
    return tags.length ? `Tags: ${tags.join(", ")}` : "";
  };

  const api = {mergeTags, suggestionDescription, formatSuggestionTags, MAX_TAGS_LENGTH};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof window !== "undefined") window.SimpleOfficeSuggestionFields = api;
})();
