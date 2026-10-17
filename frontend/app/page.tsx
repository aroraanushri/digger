"use client";
import { useEffect, useState } from "react";
import { CopilotChat, CopilotKitProvider, useRenderTool } from "@copilotkit/react-core/v2";
import { ArrowUpRight, Layers3, MapPin, Plus, Radio, ShieldCheck, Sprout } from "lucide-react";
import { z } from "zod";
import { Analysis, AnalysisView, api } from "../components/AnalysisView";
import { FieldUpdate } from "../components/FieldUpdate";

type User = {user_id: string; name: string; role: string};
type Health = {assets: number; inspections: number; version: number; model: string};

function ToolRenderer() {
  useRenderTool({name: "show_analysis", parameters: z.object({title: z.string()}), render: ({result}) => {
    if (!result) return <p className="muted">Calculating from source records…</p>;
    try {return <AnalysisView result={(typeof result === "string" ? JSON.parse(result) : result) as Analysis} />;} catch {return <p className="error">Unable to render the analytical result.</p>;}
  }}, []);
  return null;
}

const starters = [
  {icon: Radio, label: "Understand a well", detail: "Latest state, temperature & evidence", plan: {operation: "status", asset_ids: ["WELL-0001"], view: "card"}},
  {icon: MapPin, label: "Find stopped wells", detail: "Northeast Block 8 · latest inspections", plan: {operation: "status", area: "Northeast Block 8", only_nonproducing: true, view: "table"}},
  {icon: Layers3, label: "Compare downtime", detail: "Northeast Block 7 · last four months", plan: {operation: "downtime", area: "Northeast Block 7", start: "2026-06-16", limit: 3, view: "bar"}},
  {icon: Sprout, label: "Trace real spills", detail: "Corrected observations · evidence first", plan: {operation: "spills", asset_ids: ["WELL-0001"], start: "2022-10-16", view: "table"}},
];

export default function Home() {
  const [users, setUsers] = useState<User[]>([]);
  const [userId, setUserId] = useState("");
  const [health, setHealth] = useState<Health | null>(null);
  const [asOf, setAsOf] = useState("2026-10-16");
  const [result, setResult] = useState<Analysis | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [fieldOpen, setFieldOpen] = useState(false);
  const [notice, setNotice] = useState("");
  const [tab, setTab] = useState("explore");
  const [history, setHistory] = useState<Record<string, unknown>[]>([]);
  const [thread, setThread] = useState(0);
  async function refresh() {setHealth(await api<Health>("health"));}
  useEffect(() => {Promise.all([api<User[]>("users"), api<Health>("health")]).then(([u,h]) => {setUsers(u); setHealth(h); setUserId(u.find(x => x.name === "Michael Turner")?.user_id || u[0].user_id);}).catch(e => setError(`Backend is not connected. Start FastAPI on port 8000. ${String(e)}`));}, []);
  async function run(plan: object) {
    setLoading(true); setError("");
    try {setResult(await api<Analysis>("analyze", {method: "POST", body: JSON.stringify({...plan, end: asOf})}));} catch(e) {setError(String(e));} finally {setLoading(false);}
  }
  async function showHistory() {setTab("history"); try {setHistory(await api(`history/${userId}`));} catch(e) {setError(String(e));}}
  return <div className="app-shell">
    <aside className="sidebar"><a className="brand" href="/"><span className="brand-icon">d.</span><span>Digger<span className="brand-sub">ASSET INTELLIGENCE</span></span></a><div className="workspace-label">YOUR WORKSPACE</div><nav><button className={tab === "explore" ? "nav-active" : ""} onClick={() => setTab("explore")}><Layers3 size={18} /> Investigate <span>01</span></button><button className={tab === "history" ? "nav-active" : ""} onClick={showHistory}><ShieldCheck size={18} /> My conversations</button><button onClick={() => setFieldOpen(true)} disabled={!health}><Plus size={18} /> Add field update</button></nav><div className="sidebar-note"><span className="status-dot" /> LOCAL INTELLIGENCE<p>Your field. Your evidence.<br/>Your infrastructure.</p><small>Gemma 4 · Ollama<br/>Synthetic evaluation dataset</small></div><div className="profile"><label htmlFor="user">DEMO USER</label><select id="user" value={userId} onChange={e => {setUserId(e.target.value); setResult(null); setHistory([]); setTab("explore"); setThread(0);}}>{users.map(u => <option key={u.user_id} value={u.user_id}>{u.name}</option>)}</select><span>Shared facts · private conversation memory</span></div></aside>
    <main><header className="topbar"><div><span className="status-dot" /> {health ? `${health.assets} wells connected` : "Connecting to field data"}</div><label>Analysis date <input type="date" value={asOf} onChange={e => {setAsOf(e.target.value); setResult(null);}} /></label><button className="outline" disabled={!health} onClick={() => setFieldOpen(true)}><Plus size={16} /> Field update</button></header>
    <div className="content"><div className="breadcrumb">WORKSPACE <span>/</span> {tab === "history" ? "CONVERSATIONS" : "INVESTIGATE"}</div>
    {tab === "history" ? <><h1>Your conversation trail.</h1><p className="lead">Only conversations belonging to the selected demo user.</p>{history.length ? history.map((h,i) => <article className="analysis-card" key={i}><h3>{String(h.question)}</h3><p>{String(h.answer)}</p><span className="muted">{String(h.created_at)}</span></article>) : <p className="empty">No saved conversations for this user yet.</p>}</> : <><section className="hero"><div className="eyebrow"><span /> THE FIELD, IN CONTEXT</div><h1>Good decisions start<br/>with <em>better questions.</em></h1><p className="lead">Follow the evidence from a single well to the entire field.<br/>Ask, investigate, and bring new observations into the picture.</p></section><div className="starter-grid">{starters.map(({icon: Icon, ...s}) => <button key={s.label} onClick={() => run(s.plan)} disabled={loading || !health}><div><Icon size={21} strokeWidth={1.5} /><ArrowUpRight size={17} /></div><strong>{s.label}</strong><span>{s.detail}</span></button>)}</div><p className="starter-note">These shortcuts run verified database analyses directly. Free-form questions below use Gemma.</p>
      {notice && <div className="notice" role="status">{notice}</div>}{error && <div className="error" role="alert">{error}</div>}{loading && <div className="loading">Reading observations. Applying rules. Following the evidence…</div>}{result && <AnalysisView key={`${result.operation}-${result.version}-${result.as_of}`} result={result} />}
      {userId && <section className="conversation"><div className="section-heading"><div><span className="eyebrow">ASK DIGGER</span><h2>What would you like to understand?</h2></div><button onClick={() => setThread(t => t+1)}>New conversation ↗</button></div><CopilotKitProvider key={`${userId}:${thread}:${asOf}`} runtimeUrl="/api/copilotkit"><ToolRenderer /><CopilotChat threadId={`${userId}:${asOf}:${thread}`} labels={{welcomeMessageText: "Ask about production, downtime, temperatures, spills, repair costs, or maintenance recurrence. Start Ollama to enable free-form questions."}} /></CopilotKitProvider><p className="privacy-note"><ShieldCheck size={14} /> Calculations run in the backend. Gemma receives a compact schema and relevant context.</p></section>}
    </>}
    <footer><span>DIGGER / OPERATIONAL INTELLIGENCE</span><span>{health ? `${health.inspections.toLocaleString()} inspections · knowledge v${health.version}` : "Waiting for backend"}</span></footer></div></main>
    {fieldOpen && health && <FieldUpdate userId={userId} version={health.version} onClose={() => setFieldOpen(false)} onSaved={() => {setFieldOpen(false); setResult(null); setNotice("Field update saved. Run an analysis again to see the latest shared knowledge."); refresh().catch(e => setError(String(e)));}} />}
  </div>;
}
