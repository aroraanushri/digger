"use client";
import { useEffect, useState } from "react";
import { CopilotChat, CopilotKitProvider, useRenderTool } from "@copilotkit/react-core/v2";
import { ArrowUpRight, Database, Layers3, MapPin, Radio, ShieldCheck, Sprout, Wrench } from "lucide-react";
import { z } from "zod";
import { Analysis, AnalysisView, api } from "../components/AnalysisView";

type User = {user_id: string; name: string; role: string};
type Health = {assets: number; inspections: number; version: number; model: string};
type KnowledgeEntry = Record<string, unknown> & {version: number; created_at: string; kind: string; user_id: string; reason: string};

function knowledgeTitle(entry: KnowledgeEntry): string {
  if (entry.kind === "correction") return `${String(entry.asset_id)} · corrected ${String(entry.field).replaceAll("_", " ")}`;
  if (entry.kind === "inspection") return `${String(entry.asset_id)} · new inspection`;
  if (entry.kind === "policy") return `${String(entry.scope_well_type)} wells · ${String(entry.equipment_kind).replaceAll("_", " ")} policy`;
  return `${String(entry.asset_id)} · field note`;
}

function knowledgeDetail(entry: KnowledgeEntry): string {
  if (entry.kind === "correction") return `${String(entry.target_id)}: ${String(entry.field).replaceAll("_", " ")} set to ${String(entry.value)}`;
  if (entry.kind === "policy") return `Root-cause analysis after ${String(entry.corrective_threshold)} corrective repairs`;
  if (entry.kind === "inspection") {
    const inspection = entry.inspection as Record<string, unknown> | undefined;
    return `${String(inspection?.inspected_at || "Undated")} · wellhead ${String(inspection?.wellhead_temp_c)}°C · ambient ${String(inspection?.ambient_temp_c)}°C`;
  }
  const dates = [entry.valid_from, entry.valid_to].filter(Boolean).map(String).join(" to ");
  return dates || "Current shared note";
}

function ToolRenderer() {
  useRenderTool({name: "show_analysis", parameters: z.object({title: z.string()}), render: ({result}) => {
    if (!result) return <p className="muted">Calculating from source records…</p>;
    try {return <AnalysisView result={(typeof result === "string" ? JSON.parse(result) : result) as Analysis} />;} catch {return <p className="error">Unable to render the analytical result.</p>;}
  }}, []);
  useRenderTool({name: "field_update", parameters: z.object({title: z.string()}), render: ({result}) => {
    if (!result) return <p className="muted">Preparing field update for review…</p>;
    try {
      const proposal = (typeof result === "string" ? JSON.parse(result) : result) as Record<string, unknown>;
      const inspection = proposal.inspection as Record<string, unknown> | undefined;
      const summary = proposal.kind === "policy"
        ? `Policy for all ${String(proposal.scope_well_type)} wells: ${String(proposal.equipment_kind).replaceAll("_", " ")} RCA after ${String(proposal.corrective_threshold)} repairs.`
        : proposal.kind === "inspection"
        ? `New inspection for ${String(proposal.asset_id)}: wellhead ${String(inspection?.wellhead_temp_c)}°C, ambient ${String(inspection?.ambient_temp_c)}°C.`
        : proposal.kind === "correction"
          ? `Correct ${String(proposal.target_id)} for ${String(proposal.asset_id)}: ${String(proposal.field)} → ${String(proposal.value)}.`
          : `Add a note for ${String(proposal.asset_id)}.`;
      return <FieldUpdateReview proposal={proposal} summary={summary} />;
    } catch { return <p className="error">Unable to prepare the field update.</p>; }
  }}, []);
  return null;
}

function FieldUpdateReview({proposal, summary}: {proposal: Record<string, unknown>; summary: string}) {
  const [state, setState] = useState<"ready" | "reviewing" | "saving" | "saved" | "error">("ready");
  const [error, setError] = useState("");
  const [source, setSource] = useState<Record<string, unknown> | null>(null);
  async function review() {
    setError("");
    if (proposal.target_id) {
      try {setSource(await api<Record<string, unknown>>(`evidence/${encodeURIComponent(String(proposal.target_id))}`));}
      catch (e) {setError(`Unable to load the original record. ${String(e)}`); setState("error"); return;}
    }
    setState("reviewing");
  }
  async function save() {
    setState("saving");
    try { await api("knowledge", {method: "POST", body: JSON.stringify(proposal)}); setState("saved"); window.dispatchEvent(new Event("shared-knowledge-updated")); }
    catch (e) { setError(String(e)); setState("error"); }
  }
  const record = source?.record as Record<string, unknown> | undefined;
  const field = String(proposal.field || "");
  return <div className="chat-update-review"><strong>Field update ready</strong><p>{summary}</p><p className="muted">{String(proposal.reason)} · Knowledge v{String(proposal.expected_version)}</p>
    {state === "ready" && <button className="primary" onClick={review}>Review update</button>}
    {(state === "reviewing" || state === "saving") && <div className="update-confirmation"><div className="review-title"><strong>Review before saving</strong><span>Shared across all users</span></div><dl><div><dt>Update type</dt><dd>{String(proposal.kind)}</dd></div>{Boolean(proposal.asset_id) && <div><dt>Well</dt><dd>{String(proposal.asset_id)}</dd></div>}{Boolean(proposal.target_id) && <div><dt>Source record</dt><dd>{String(proposal.target_id)}</dd></div>}{Boolean(field) && <div><dt>Field</dt><dd>{field.replaceAll("_", " ")}</dd></div>}{Boolean(field && record) && <div><dt>Original value</dt><dd>{String(record?.[field] ?? "—")}</dd></div>}{proposal.value !== undefined && proposal.value !== null && <div><dt>Proposed value</dt><dd>{String(proposal.value)}</dd></div>}{Boolean(proposal.scope_well_type) && <div><dt>Policy scope</dt><dd>{String(proposal.scope_well_type)} · {String(proposal.equipment_kind).replaceAll("_", " ")}</dd></div>}{Boolean(proposal.corrective_threshold) && <div><dt>Threshold</dt><dd>{String(proposal.corrective_threshold)} corrective repairs</dd></div>}{Boolean(proposal.valid_from) && <div><dt>Effective dates</dt><dd>{String(proposal.valid_from)}{Boolean(proposal.valid_to) ? ` to ${String(proposal.valid_to)}` : " onward"}</dd></div>}<div><dt>Submitted by</dt><dd>{String(proposal.user_id)}</dd></div><div><dt>Reason</dt><dd>{String(proposal.reason)}</dd></div></dl><p className="review-disclosure">Saving appends this update to shared field knowledge. The original source record is retained.</p><div className="review-actions"><button className="outline" disabled={state === "saving"} onClick={() => setState("ready")}>Cancel</button><button className="primary" disabled={state === "saving"} onClick={save}>{state === "saving" ? "Saving…" : "Save shared update"}</button></div></div>}
    {state === "saved" && <p className="notice" role="status">Saved to shared field knowledge.</p>}{state === "error" && <><p role="alert" className="error">{error}</p><button className="outline" onClick={() => setState("ready")}>Try review again</button></>}
  </div>;
}

const starters = [
  {icon: Radio, label: "What is the latest state of WELL-0001?", detail: "Production status, temperature and evidence", plan: {operation: "status", asset_ids: ["WELL-0001"], view: "card"}},
  {icon: MapPin, label: "Which wells stopped producing in Northeast Block 8?", detail: "Latest non-producing inspections", plan: {operation: "status", area: "Northeast Block 8", only_nonproducing: true, view: "table"}},
  {icon: Layers3, label: "How much downtime was recorded in Northeast Block 7?", detail: "Observed downtime over the last four months", plan: {operation: "downtime", area: "Northeast Block 7", start: "2026-06-16", limit: 3, view: "bar"}},
  {icon: Sprout, label: "Which spills remain after corrections?", detail: "Effective observations with source evidence", plan: {operation: "spills", asset_ids: ["WELL-0001"], start: "2022-10-16", view: "table"}},
  {icon: Radio, label: "How did WELL-0001 temperatures change?", detail: "Wellhead and ambient readings over time", plan: {operation: "temperatures", asset_ids: ["WELL-0001"], start: "2026-07-23", view: "line"}},
  {icon: Layers3, label: "What were the repair costs in Northeast Block 7?", detail: "Costs grouped by well", plan: {operation: "costs", area: "Northeast Block 7", start: "2026-01-01", view: "bar"}},
  {icon: Wrench, label: "Which wells need maintenance attention?", detail: "Ranked from recent operating and repair signals", plan: {operation: "predictive_maintenance", limit: 12, view: "bar"}},
  {icon: MapPin, label: "Why were repairs needed in Northeast Block 7?", detail: "Recorded issues, actions, downtime and costs", plan: {operation: "repair_reasons", area: "Northeast Block 7", view: "table"}},
  {icon: Sprout, label: "What do the well-type manuals say about production?", detail: "Search the supplied manuals and reports", plan: {operation: "search", search: "production temperature threshold", view: "table"}},
];

export default function Home() {
  const [users, setUsers] = useState<User[]>([]);
  const [userId, setUserId] = useState("");
  const [health, setHealth] = useState<Health | null>(null);
  const [asOf, setAsOf] = useState("2027-01-15");
  const [result, setResult] = useState<Analysis | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState("explore");
  const [history, setHistory] = useState<Record<string, unknown>[]>([]);
  const [knowledge, setKnowledge] = useState<KnowledgeEntry[]>([]);
  const [thread, setThread] = useState(0);
  useEffect(() => {Promise.all([api<User[]>("users"), api<Health>("health")]).then(([u,h]) => {setUsers(u); setHealth(h); setUserId(u.find(x => x.name === "Michael Turner")?.user_id || u[0].user_id);}).catch(e => setError(`Backend is not connected. Start FastAPI on port 8000. ${String(e)}`));}, []);
  useEffect(() => {const refresh = () => {api<Health>("health").then(setHealth).catch(() => undefined); api<KnowledgeEntry[]>("knowledge").then(setKnowledge).catch(() => undefined);}; window.addEventListener("shared-knowledge-updated", refresh); return () => window.removeEventListener("shared-knowledge-updated", refresh);}, []);
  async function run(plan: object) {
    setLoading(true); setError("");
    try {setResult(await api<Analysis>("analyze", {method: "POST", body: JSON.stringify({...plan, end: asOf})}));} catch(e) {setError(String(e));} finally {setLoading(false);}
  }
  async function showHistory() {setTab("history"); try {setHistory(await api(`history/${userId}`));} catch(e) {setError(String(e));}}
  async function showKnowledge() {setTab("knowledge"); setError(""); try {setKnowledge(await api<KnowledgeEntry[]>("knowledge"));} catch(e) {setError(String(e));}}
  return <div className="app-shell">
    <aside className="sidebar"><a className="brand" href="/"><span className="brand-icon">d.</span><span>Digger<span className="brand-sub">ASSET INTELLIGENCE</span></span></a><div className="workspace-label">YOUR WORKSPACE</div><nav><button className={tab === "explore" ? "nav-active" : ""} onClick={() => setTab("explore")}><Layers3 size={18} /> Investigate <span>01</span></button><button className={tab === "knowledge" ? "nav-active" : ""} onClick={showKnowledge}><Database size={18} /> Shared field knowledge <span>{health?.version || 0}</span></button><button className={tab === "history" ? "nav-active" : ""} onClick={showHistory}><ShieldCheck size={18} /> My conversations</button></nav><div className="sidebar-note"><span className="status-dot" /> LOCAL INTELLIGENCE<p>Your field. Your evidence.<br/>Your infrastructure.</p><small>Gemma 4 · Ollama<br/>Synthetic evaluation dataset</small></div><div className="profile"><label htmlFor="user">DEMO USER</label><select id="user" value={userId} onChange={e => {setUserId(e.target.value); setResult(null); setHistory([]); setTab("explore"); setThread(0);}}>{users.map(u => <option key={u.user_id} value={u.user_id}>{u.name}</option>)}</select><span>Shared facts · private conversation memory</span></div></aside>
    <main><header className="topbar"><div><span className="status-dot" /> {health ? `${health.assets} wells connected` : "Connecting to field data"}</div><label>Analysis date <input type="date" value={asOf} onChange={e => {setAsOf(e.target.value); setResult(null);}} /></label></header>
    <div className="content"><div className="breadcrumb">WORKSPACE <span>/</span> {tab === "history" ? "CONVERSATIONS" : tab === "knowledge" ? "SHARED FIELD KNOWLEDGE" : "INVESTIGATE"}</div>
    {tab === "history" ? <><h1>Your conversation trail.</h1><p className="lead">Only conversations belonging to the selected demo user.</p>{history.length ? history.map((h,i) => <article className="analysis-card" key={i}><h3>{String(h.question)}</h3><p>{String(h.answer)}</p><span className="muted">{String(h.created_at)}</span></article>) : <p className="empty">No saved conversations for this user yet.</p>}</> : tab === "knowledge" ? <section className="knowledge-section"><div className="knowledge-heading"><div><span className="eyebrow">SHARED ACROSS ALL USERS</span><h1>Shared field knowledge.</h1><p className="lead">Accepted corrections, inspections, field notes, and operating policies. Original source records remain unchanged.</p></div><button className="outline" onClick={showKnowledge}>Refresh</button></div>{error && <div className="error" role="alert">{error}</div>}{knowledge.length ? <div className="knowledge-list">{knowledge.map(entry => <article className="knowledge-card" key={entry.version}><div className="knowledge-card-heading"><span className={`knowledge-kind kind-${entry.kind}`}>{entry.kind}</span><span className="muted">Knowledge v{entry.version}</span></div><h3>{knowledgeTitle(entry)}</h3><strong>{knowledgeDetail(entry)}</strong><p>{entry.reason}</p><div className="knowledge-meta"><span>{users.find(user => user.user_id === entry.user_id)?.name || entry.user_id}</span><time>{new Date(entry.created_at).toLocaleString()}</time></div></article>)}</div> : <p className="empty">No shared field updates have been accepted yet.</p>}</section> : <><section className="sample-questions"><div className="eyebrow"><span /> START WITH A QUESTION</div><h1>Sample questions</h1><p className="lead">Choose an operational question to inspect the verified data.</p></section><div className="starter-grid">{starters.map(({icon: Icon, ...s}) => <button key={s.label} onClick={() => run(s.plan)} disabled={loading || !health}><div><Icon size={21} strokeWidth={1.5} /><ArrowUpRight size={17} /></div><strong>{s.label}</strong><span>{s.detail}</span></button>)}</div><p className="starter-note">These examples run verified database analyses. Use the chat below for follow-up questions or field updates.</p>
      {error && <div className="error" role="alert">{error}</div>}{loading && <div className="loading">Reading observations. Applying rules. Following the evidence…</div>}{result && <AnalysisView key={`${result.operation}-${result.version}-${result.as_of}`} result={result} />}
      {userId && <section className="conversation"><div className="section-heading"><div><span className="eyebrow">ASK DIGGER</span><h2>What would you like to understand?</h2></div><button onClick={() => setThread(t => t+1)}>New conversation ↗</button></div><CopilotKitProvider key={`${userId}:${thread}:${asOf}`} runtimeUrl="/api/copilotkit"><ToolRenderer /><CopilotChat threadId={`${userId}:${asOf}:${thread}`} labels={{welcomeMessageText: "Ask about production, downtime, temperatures, spills, repair costs, or maintenance priorities. Start Ollama to enable free-form questions."}} /></CopilotKitProvider><p className="privacy-note"><ShieldCheck size={14} /> Calculations run in the backend. Gemma receives a compact schema and relevant context.</p></section>}
    </>}
    <footer><span>DIGGER / OPERATIONAL INTELLIGENCE</span><span>{health ? `${health.inspections.toLocaleString()} inspections · knowledge v${health.version}` : "Waiting for backend"}</span></footer></div></main>
  </div>;
}
