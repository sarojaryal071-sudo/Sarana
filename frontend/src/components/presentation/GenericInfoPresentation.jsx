// src/components/presentation/GenericInfoPresentation.jsx — the
// flexible catch-all (section 6C): `data`: {body?: str, items?:
// [{label, value}, ...]}. Either or both may be present — a plain
// paragraph, a set of labeled facts, or both stacked.
export default function GenericInfoPresentation({ data }) {
  const body = data?.body || "";
  const items = Array.isArray(data?.items) ? data.items : [];

  return (
    <div className="pw-generic">
      {body && <div className="pw-generic-body">{body}</div>}
      {items.length > 0 && (
        <div className="pw-generic-items">
          {items.map((item, i) => (
            <div className="pw-generic-item" key={item.label ?? i}>
              <span className="pw-generic-item-label">{item.label}</span>
              <span className="pw-generic-item-value">{item.value}</span>
            </div>
          ))}
        </div>
      )}
      {!body && items.length === 0 && <div className="pw-generic-empty">No information to show.</div>}
    </div>
  );
}
