// src/components/presentation/SearchResultsPresentation.jsx — `data`:
// {query, mode, text}, exactly main.py's own web_search dispatch (see
// that branch) — `text` is web_search_action's real, already-formatted
// result block. This module does NOT re-parse it into fake structured
// "result cards": actions/web_search.py returns prose/lines, not
// individually-addressable result objects, and inventing that structure
// here would be fabricated data. Line-by-line rendering (instead of one
// <pre> blob) is purely a readability choice — same information, no new
// information invented.
//
// `expanded` (Universal Information Surface's own compact/expanded
// state) caps how many lines show by default — the same already-
// fetched `text`, never a second search.
const COMPACT_LINE_COUNT = 6;

export default function SearchResultsPresentation({ data, expanded = false }) {
  const query = data?.query || "";
  const mode = data?.mode || "search";
  const lines = (data?.text || "").split("\n").filter((l) => l.trim().length > 0);
  const shownLines = expanded ? lines : lines.slice(0, COMPACT_LINE_COUNT);
  const hiddenCount = lines.length - shownLines.length;

  return (
    <div className="pw-search">
      <div className="pw-search-meta">
        <span className="pw-search-mode">{mode}</span>
        {query && <span className="pw-search-query">{query}</span>}
      </div>
      <div className="pw-search-lines">
        {shownLines.map((line, i) => (
          <div className="pw-search-line pw-reveal-item" style={{ "--pw-reveal-index": i }} key={i}>{line}</div>
        ))}
      </div>
      {!expanded && hiddenCount > 0 && (
        <div className="pw-search-more-hint">+{hiddenCount} more lines — expand for the full result</div>
      )}
    </div>
  );
}
