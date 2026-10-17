"use client";
import { useEffect, useRef, useState } from "react";
import * as echarts from "echarts";

export type Analysis = {
  operation: string; version: number; dataset_version: string; as_of: string;
  rows: Record<string, unknown>[]; total_rows: number; metrics: Record<string, number>;
  explanation: string; warnings: string[]; evidence_ids: string[];
  rules: {path: string; threshold_c: number}[]; view: string;
};

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/backend/${path}`, {...init, headers: {"Content-Type": "application/json", ...init?.headers}});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
  return data as T;
}

function Plot({result, kind}: {result: Analysis; kind: string}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    const rows = result.rows;
    const color = ["#c25d36", "#4a7667", "#c5a56b", "#648095"];
    let option: echarts.EChartsOption;
    if (kind === "line") {
      const assets = [...new Set(rows.map(r => String(r.asset_id)))];
      option = {color, tooltip: {trigger: "axis"}, legend: {bottom: 0}, grid: {left: 52, right: 20, top: 25, bottom: 65}, xAxis: {type: "time"}, yAxis: {type: "value", name: "°C"}, series: assets.flatMap(asset => ["wellhead_temp_c", "ambient_temp_c"].map(field => ({name: `${asset} ${field === "wellhead_temp_c" ? "wellhead" : "air"}`, type: "line" as const, showSymbol: true, data: rows.filter(r => r.asset_id === asset).map(r => [String(r.inspected_at), Number(r[field])])})))};
    } else if (kind === "map") {
      option = {color, tooltip: {formatter: (p: unknown) => {const item = p as {data: {name: string}}; return item.data.name;}}, grid: {left: 65, right: 20, top: 20, bottom: 45}, xAxis: {type: "value", name: "Longitude", scale: true}, yAxis: {type: "value", name: "Latitude", scale: true}, series: [{type: "scatter", symbolSize: 14, data: rows.map(r => ({name: String(r.asset_id), value: [Number(r.lon), Number(r.lat)]}))}]};
    } else if (kind === "heatmap") {
      const areas = [...new Set(rows.map(r => String(r.area)))];
      const months = [...new Set(rows.map(r => String(r.inspected_at).slice(0, 7)))].sort();
      const counts = new Map<string, number>();
      rows.forEach(r => {const k = `${r.area}|${String(r.inspected_at).slice(0, 7)}`; counts.set(k, (counts.get(k) || 0) + 1);});
      option = {tooltip: {}, grid: {left: 170, right: 20, top: 20, bottom: 80}, xAxis: {type: "category", data: months}, yAxis: {type: "category", data: areas}, visualMap: {min: 0, max: Math.max(1, ...counts.values()), orient: "horizontal", bottom: 0, left: "center", inRange: {color: ["#f4eee4", "#bd572e"]}}, series: [{type: "heatmap", label: {show: true}, data: areas.flatMap((a, y) => months.map((m, x) => [x, y, counts.get(`${a}|${m}`) || 0]))}]};
    } else {
      const field = "downtime_days" in (rows[0] || {}) ? "downtime_days" : "cost_usd" in (rows[0] || {}) ? "cost_usd" : "delta_c";
      option = {color, tooltip: {trigger: "axis"}, grid: {left: 60, right: 15, top: 20, bottom: 75}, xAxis: {type: "category", data: rows.map(r => String(r.asset_id)), axisLabel: {rotate: 35}}, yAxis: {type: "value", name: field.replaceAll("_", " ")}, series: [{type: "bar", data: rows.map(r => Number(r[field])), barMaxWidth: 38, itemStyle: {borderRadius: [4, 4, 0, 0]}}]};
    }
    chart.setOption(option);
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(ref.current);
    return () => {observer.disconnect(); chart.dispose();};
  }, [result, kind]);
  return <div ref={ref} className="plot" role="img" aria-label={`${kind} visualization of ${result.operation}`} />;
}

function bestView(result: Analysis): string {
  const supported = new Set(["table", "card", "line", "map", "heatmap", "bar"]);
  if (supported.has(result.view)) return result.view;
  if (result.operation === "status") return result.rows.length <= 6 ? "card" : "table";
  if (result.operation === "temperatures") return "line";
  if (["costs", "downtime", "maintenance"].includes(result.operation)) return "bar";
  return "table";
}

export function AnalysisView({result}: {result: Analysis}) {
  const [filter, setFilter] = useState("");
  const [evidence, setEvidence] = useState<unknown>(null);
  const [error, setError] = useState("");
  const rows = result.rows.filter(r => JSON.stringify(r).toLowerCase().includes(filter.toLowerCase()));
  const columns = Object.keys(result.rows[0] || {}).filter(k => !["context", "updates", "evidence_ids", "corrections"].includes(k));
  const activeKind = bestView(result);
  const title = result.operation === "repair_reasons" ? "Why repairs were needed" : result.operation === "status" ? "A clearer picture of your field" : result.operation.charAt(0).toUpperCase() + result.operation.slice(1);
  async function inspect(id: string) {try {setEvidence(await api(`evidence/${encodeURIComponent(id)}`)); setError("");} catch(e) {setError(String(e));}}
  return <section className="analysis-card">
    <div className="result-heading"><span className="eyebrow">VERIFIED ANALYSIS</span><span className="muted">Knowledge v{result.version} · {result.as_of}</span></div>
    <h3>{title}</h3>
    <p>{result.explanation}</p>
    {!!Object.keys(result.metrics).length && <div className="metrics">{Object.entries(result.metrics).map(([key, value]) => <div key={key}><strong>{value.toLocaleString()}</strong><span>{key.replaceAll("_", " ")}</span></div>)}</div>}
    {activeKind === "card" ? <div className="status-grid">{rows.map((r, i) => <article key={i}><span className={`pill ${r.producing ? "green" : "amber"}`}>{r.producing ? "Producing" : "Not producing"}</span><h3>{String(r.asset_id)}</h3><div className="temperature">{Number(r.delta_c).toFixed(1)}<small>°C above ambient</small></div><p>Type threshold: {String(r.threshold_c)}°C</p><p>{String(r.notes || "No inspector note")}</p>{Array.isArray(r.context) && r.context.map((n: {note_id: string; text: string}) => <p key={n.note_id}>{n.text}</p>)}</article>)}</div> : activeKind === "table" ? <><input className="filter" value={filter} onChange={e => setFilter(e.target.value)} placeholder="Filter these results…" aria-label="Filter results" /><div className="table-scroll"><table><thead><tr>{columns.map(c => <th key={c}>{c.replaceAll("_", " ")}</th>)}</tr></thead><tbody>{rows.map((r,i) => <tr key={i}>{columns.map(c => <td key={c}>{typeof r[c] === "boolean" ? r[c] ? "Yes" : "No" : String(r[c] ?? "—")}</td>)}</tr>)}</tbody></table></div></> : <><Plot result={result} kind={activeKind} />{activeKind === "map" && <p className="muted">Geographic coordinate view · longitude / latitude · no external tile requests</p>}</>}
    {!rows.length && <p className="empty">No matching observations in this scope.</p>}
    <p className="muted">{result.rows.length} displayed / {result.total_rows} matching rows · charts use displayed rows</p>
    <details><summary>Sources, rules & limitations <span>{result.evidence_ids.length} records</span></summary><ul>{result.warnings.map(w => <li key={w}>{w}</li>)}</ul><div className="source-chips">{result.evidence_ids.map(id => <button key={id} onClick={() => inspect(id)}>{id}</button>)}</div>{result.rules.map(r => <button className="document-link" key={r.path} onClick={async () => {try {setEvidence(await api(`document?path=${encodeURIComponent(r.path)}`));} catch(e) {setError(String(e));}}}>{r.path}</button>)}</details>
    {error && <p role="alert" className="error">{error}</p>}
    {evidence !== null && <div className="evidence-panel"><div><strong>Source evidence</strong><button onClick={() => setEvidence(null)}>Close</button></div><pre>{JSON.stringify(evidence,null,2)}</pre></div>}
  </section>;
}
