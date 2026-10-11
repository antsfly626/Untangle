import type { Session } from "./contracts";
const base = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");
export function parseSession(value: unknown): Session {
  if (!value || typeof value !== "object") throw new Error("Invalid session response.");
  const s = value as Session;
  if (typeof s.id !== "string" || !["idle", "negotiating", "needs_answers"].includes(s.status) || !s.plan || s.plan.session_id !== s.id || !s.plan.outcome || !s.plan.constraints || !Array.isArray(s.plan.nodes) || !Array.isArray(s.plan.negotiation) || !Array.isArray(s.plan.tasks) || !Array.isArray(s.plan.architecture) || !Array.isArray(s.plan.questions)) throw new Error("The backend returned an incompatible session contract.");
  return s;
}
async function request(path: string, text?: string): Promise<Session> {
  const response = await fetch(`${base}${path}`, { method: text === undefined ? "GET" : "POST", headers: text === undefined ? undefined : { "Content-Type": "application/json" }, body: text === undefined ? undefined : JSON.stringify({ text }), cache: "no-store" });
  if (!response.ok) {
    if (response.status === 404) throw new Error("Session not found. Sessions are cleared when the backend restarts.");
    if (response.status === 409) throw new Error("The agents are still negotiating. Wait for completion before updating constraints.");
    throw new Error(`Backend request failed (${response.status}). Check the backend terminal and provider configuration, then retry.`);
  }
  return parseSession(await response.json());
}
export const createSession = (text: string) => request("/sessions", text);
export const getSession = (id: string) => request(`/sessions/${encodeURIComponent(id)}`);
export const sendMessage = (id: string, text: string) => request(`/sessions/${encodeURIComponent(id)}/messages`, text);
export function subscribeSession(id: string, onSession: (s: Session) => void, onConnection: (state: "connected" | "reconnecting" | "invalid") => void): () => void {
  const stream = new EventSource(`${base}/sessions/${encodeURIComponent(id)}/stream`);
  stream.onopen = () => onConnection("connected");
  stream.onerror = () => onConnection("reconnecting");
  stream.addEventListener("session", event => {
    try {
      const session = parseSession(JSON.parse(event.data));
      if (session.id !== id) throw new Error("Mismatched session");
      onSession(session); onConnection("connected");
    } catch { onConnection("invalid"); }
  });
  return () => stream.close();
}
