export default function ConnectionBanner({ connectionState }) {
  if (connectionState === "connected" || connectionState === "open") return null;
  if (connectionState === "connecting") return null; // first connect — no need to alarm
  if (connectionState === "reconnecting")
    return <div className="conn-banner reconnecting">Reconnecting to SARANA…</div>;
  if (connectionState === "error")
    return <div className="conn-banner error">Connection error — retrying…</div>;
  return <div className="conn-banner reconnecting">Disconnected — retrying…</div>;
}
