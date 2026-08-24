import { useEffect, useRef } from "react";

export default function LogPanel({ messages }) {
  const feedRef = useRef(null);

  useEffect(() => {
    const el = feedRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  return (
    <div className="log-feed" ref={feedRef}>
      {messages.length === 0 && (
        <div className="log-line sys">Waiting for conversation…</div>
      )}
      {messages.map((m, i) => (
        <div className={`log-line ${m.speaker}`} key={i}>
          {m.speaker !== "sys" && (
            <span className="who">{m.speaker === "user" ? "You:" : "Sarana:"}</span>
          )}
          {m.text}
        </div>
      ))}
    </div>
  );
}
