// src/components/presentation/TablePresentation.jsx — comparisons/lists/
// structured multi-value results. `data`: {columns: [str, ...], rows:
// [[cell, ...], ...]}. No sorting/filtering UI in this pass — a plain,
// readable table is the actual requirement (section 6D); interaction
// can be added later without changing this payload shape.
export default function TablePresentation({ data }) {
  const columns = Array.isArray(data?.columns) ? data.columns : [];
  const rows = Array.isArray(data?.rows) ? data.rows : [];

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
          {rows.map((row, ri) => (
            <tr key={ri}>
              {row.map((cell, ci) => <td key={ci}>{cell}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
