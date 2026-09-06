// src/components/presentation/SearchResultsPresentation.jsx — `data`:
// {query, mode, text}, exactly main.py's own web_search dispatch (see
// that branch) — `text` is web_search_action's real, already-formatted
// result block. This module does NOT re-parse it into fake structured
// "result cards": actions/web_search.py returns prose/lines, not
// individually-addressable result objects, and inventing that structure
// here would be exactly the fabricated-data section 22 forbids. Line-by-
// line rendering (instead of one <pre> blob) is purely a readability
// choice — same information, no new information invented.
export default function SearchResultsPresentation({ data }) {
  const query = data?.query || "";
  const mode = data?.mode || "search";
  const lines = (data?.text || "").split("\n").filter((l) => l.trim().length > 0);

  return (
    <div className="pw-search">
      <div className="pw-search-meta">
        <span className="pw-search-mode">{mode}</span>
        {query && <span className="pw-search-query">{query}</span>}
      </div>
      <div className="pw-search-lines">
        {lines.map((line, i) => (
          <div className="pw-search-line pw-reveal-item" style={{ "--pw-reveal-index": i }} key={i}>{line}</div>
        ))}
      </div>
    </div>
  );
}
