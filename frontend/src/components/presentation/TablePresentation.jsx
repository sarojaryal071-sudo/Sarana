// src/components/presentation/TablePresentation.jsx — comparisons/lists/
// structured multi-value results. `data`: {columns: [str, ...], rows:
// [[cell, ...], ...]}. No sorting/filtering UI — a plain, readable
// table is the actual requirement; interaction can be added later
// without changing this payload shape.
//
// `expanded` (Universal Information Surface's own compact/expanded
// state) caps how many rows show by default — the caller-supplied rows
// are never re-fetched or re-sorted, just progressively revealed.
const COMPACT_ROW_COUNT = 6;

export default function TablePresentation({ data, expanded = false }) {
  const columns = Array.isArray(data?.columns) ? data.columns : [];
  const rows = Array.isArray(data?.rows) ? data.rows : [];
  const shownRows = expanded ? rows : rows.slice(0, COMPACT_ROW_COUNT);
  const hiddenCount = rows.length - shownRows.length;

  if (columns.length === 0) {
    return <div className="pw-table-empty">No table data.</div>;
  }

  return (
    <div className="pw-table-wrap">
      <table className="pw-table">
        <thead>
          <tr>
            {columns.map((c, i) => <th key={i}>{c}</th>)}
          </tr>
        </thead>
        <tbody>
          {shownRows.map((row, ri) => (
            <tr className="pw-reveal-item" style={{ "--pw-reveal-index": ri }} key={ri}>
              {row.map((cell, ci) => <td key={ci}>{cell}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
      {!expanded && hiddenCount > 0 && (
        <div className="pw-table-more-hint">+{hiddenCount} more rows — expand for the full table</div>
      )}
    </div>
  );
}
