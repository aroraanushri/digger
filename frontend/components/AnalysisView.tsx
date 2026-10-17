"use client";
import { useEffect, useRef, useState } from "react";
import * as echarts from "echarts";

export type Analysis = {
  operation: string; version: number; dataset_version: string; as_of: string;
  rows: Record<string, unknown>[]; total_rows: number; metrics: Record<string, number>;
  explanation: string; warnings: string[]; evidence_ids: string[];
  rules: {path: string; threshold_c: number}[]; view: string;
  plan?: {view?: string};
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
      option = {color, tooltip: {trigger: "axis"}, legend: {bottom: 0}, grid: {left: 52, right: 20, top: 25, bottom: 65}, xAxis: {type: "time"}, yAxis: {type: "value", name: "°C"}, series: assets.flatMap(asset => ["wellhead_temp_c", "ambient_temp_c"].map(field => ({name: `${asset} ${field === "wellhead_temp_c" ? "wellhead" : "air"}`, type: "line" as const, showSymbol: true, data: rows.filter(r => r.asset_id === asset).map(r => [String(r.inspected_at), Number(r[field])]), ...(field === "wellhead_temp_c" ? {markPoint: {symbolSize: 38, itemStyle: {color: "#b75035"}, label: {formatter: "Stopped", fontSize: 8}, data: rows.filter(r => r.asset_id === asset && !r.producing).map(r => ({name: "Not producing", coord: [String(r.inspected_at), Number(r.wellhead_temp_c)] as [string, number]}))}} : {})})))};
    } else if (kind === "map") {
      option = {color, tooltip: {formatter: (p: unknown) => {const item = p as {data: {name: string}}; return item.data.name;}}, grid: {left: 65, right: 20, top: 20, bottom: 45}, xAxis: {type: "value", name: "Longitude", scale: true}, yAxis: {type: "value", name: "Latitude", scale: true}, series: [{type: "scatter", symbolSize: 14, data: rows.map(r => ({name: String(r.asset_id), value: [Number(r.lon), Number(r.lat)]}))}]};
    } else if (kind === "heatmap") {
      const areas = [...new Set(rows.map(r => String(r.area)))];
      const months = [...new Set(rows.map(r => String(r.inspected_at).slice(0, 7)))].sort();
      const counts = new Map<string, number>();
      rows.forEach(r => {const k = `${r.area}|${String(r.inspected_at).slice(0, 7)}`; counts.set(k, (counts.get(k) || 0) + 1);});
      option = {tooltip: {}, grid: {left: 170, right: 20, top: 20, bottom: 80}, xAxis: {type: "category", data: months}, yAxis: {type: "category", data: areas}, visualMap: {min: 0, max: Math.max(1, ...counts.values()), orient: "horizontal", bottom: 0, left: "center", inRange: {color: ["#f4eee4", "#bd572e"]}}, series: [{type: "heatmap", label: {show: true}, data: areas.flatMap((a, y) => months.map((m, x) => [x, y, counts.get(`${a}|${m}`) || 0]))}]};
    } else {
      const field = "priority_score" in (rows[0] || {}) ? "priority_score" : "avg_delta_c" in (rows[0] || {}) ? "avg_delta_c" : "downtime_days" in (rows[0] || {}) ? "downtime_days" : "cost_usd" in (rows[0] || {}) ? "cost_usd" : "delta_c";
      const category = "area" in (rows[0] || {}) ? "area" : "asset_id";
      option = {color, tooltip: {trigger: "axis"}, grid: {left: 60, right: 15, top: 20, bottom: 75}, xAxis: {type: "category", data: rows.map(r => String(r[category])), axisLabel: {rotate: 35}}, yAxis: {type: "value", name: field.replaceAll("_", " ")}, series: [{type: "bar", data: rows.map(r => Number(r[field])), barMaxWidth: 38, itemStyle: {borderRadius: [4, 4, 0, 0]}}]};
    }
    chart.setOption(option);
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(ref.current);
    return () => {observer.disconnect(); chart.dispose();};
  }, [result, kind]);
  return <div ref={ref} className="plot" role="img" aria-label={`${kind} visualization of ${result.operation}`} />;
}

function bestView(result: Analysis): string {
  if (result.operation === "temperatures" && result.rows.length > 0 && result.rows.length < 4 && result.plan?.view === "auto") return "temperature-cards";
  const supported = new Set(["summary", "table", "card", "line", "map", "heatmap", "bar"]);
  if (supported.has(result.view)) return result.view;
  if (result.operation === "status") return result.rows.length <= 6 ? "card" : "table";
  if (result.operation === "temperatures") return "line";
  if (["costs", "downtime", "maintenance", "predictive_maintenance", "regional_temperatures", "hottest_wells"].includes(result.operation)) return "bar";
  return "table";
}

function tableColumns(result: Analysis): string[] {
  const preferred: Record<string, string[]> = {
    spills: ["asset_id", "inspection_id", "inspected_at", "spill_est_volume_bbl", "notes"],
    status: ["asset_id", "inspected_at", "delta_c", "recorded_status", "reason"],
    status_disagreements: ["asset_id", "area", "latest_inspection_id", "latest_inspection_date", "latest_inspection_status", "latest_operator_note_date", "latest_operator_note", "status_now", "status_basis"],
    temperatures: ["asset_id", "inspected_at", "wellhead_temp_c", "ambient_temp_c", "delta_c", "producing"],
    downtime: ["asset_id", "downtime_days", "observed_inspections"],
    costs: ["asset_id", "repair_count", "cost_usd"],
    maintenance: ["asset_id", "equipment_kind", "corrective_count", "repair_count", "cost_usd"],
    regional_temperatures: ["area", "avg_delta_c", "avg_wellhead_temp_c", "avg_ambient_temp_c", "delta_stddev_c", "observations", "wells"],
    hottest_wells: ["asset_id", "area", "avg_delta_c", "max_delta_c", "avg_wellhead_temp_c", "avg_ambient_temp_c", "observations", "observed_days"],
    compliance: ["asset_id", "protocol", "status", "detail", "due", "fine_usd"],
    containment: ["asset_id", "inspected_at", "spill_est_volume_bbl", "containment_hours", "limit_hours", "maintenance_id"],
    reportable_spills: ["asset_id", "inspected_at", "spill_est_volume_bbl", "report_due_at", "report_status"],
    gas_response: ["asset_id", "area", "reported_at", "response_id", "response_at", "hours_to_response", "deadline_hours", "result"],
    bypasses: ["asset_id", "area", "bypass_in", "bypass_out", "approval_id", "approval_valid_until", "status"],
    rca: ["asset_id", "well_type", "equipment_kind", "corrective_repairs_12m", "threshold", "rule_source"],
    planned_downtime: ["asset_id", "starts", "ends", "status", "detail", "author"],
  };
  const available = new Set(Object.keys(result.rows[0] || {}));
  let columns = (preferred[result.operation] || Object.keys(result.rows[0] || {}).filter(key => !["context", "updates", "evidence_ids", "corrections", "corrections_applied"].includes(key))).filter(key => available.has(key));
  if (result.operation === "spills" && new Set(result.rows.map(row => row.asset_id)).size === 1) columns = columns.filter(key => key !== "asset_id");
  return columns;
}

function displayCell(column: string, value: unknown): string {
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (value === null || value === undefined || value === "") return "—";
  if (["inspected_at", "started_at", "completed_at", "report_due_at", "latest_inspection_date", "latest_operator_note_date"].includes(column)) return String(value).slice(0, 10);
  return String(value);
}

function columnLabel(column: string): string {
  const specialLabels: Record<string, string> = {asset_id: "Well", area: "Area", reported_at: "Reported at", response_id: "Response ID", response_at: "Response time", hours_to_response: "Hours to response", deadline_hours: "Deadline (hours)", result: "Met / Missed", bypass_in: "Bypass in", bypass_out: "Bypass out", approval_id: "Approval", approval_valid_until: "Approval valid until", status: "Status", latest_inspection_id: "Latest inspection", latest_inspection_date: "Inspection date", latest_inspection_status: "Inspection status", latest_operator_note_date: "Operator note date", latest_operator_note: "Operator note", status_now: "Status now", status_basis: "Why this status"};
  if (specialLabels[column]) return specialLabels[column];
  return ({inspection_id: "Inspection", inspected_at: "Date", spill_est_volume_bbl: "Volume (bbl)", delta_c: "Above ambient (°C)", cost_usd: "Cost (USD)", fine_usd: "Fine (USD)"} as Record<string, string>)[column] || column.replaceAll("_", " ");
}

export function AnalysisView({result}: {result: Analysis}) {
  const [filter, setFilter] = useState("");
  const [evidence, setEvidence] = useState<unknown>(null);
  const [error, setError] = useState("");
  const rows = result.rows.filter(r => JSON.stringify(r).toLowerCase().includes(filter.toLowerCase()));
  const columns = tableColumns(result);
  const activeKind = bestView(result);
  const title = result.operation === "predictive_maintenance" ? "Maintenance priorities" : result.operation === "repair_reasons" ? "Why repairs were needed" : result.operation === "regional_temperatures" ? "Which region runs hotter" : result.operation === "hottest_wells" ? "Hottest observed wells" : result.operation === "status_disagreements" ? "Inspection and operator-note conflicts" : result.operation === "gas_response" ? "Gas odor response (OPS-1)" : result.operation === "status" ? "A clearer picture of your field" : result.operation.charAt(0).toUpperCase() + result.operation.slice(1);
  async function inspect(id: string) {try {setEvidence(await api(`evidence/${encodeURIComponent(id)}`)); setError("");} catch(e) {setError(String(e));}}
  return <section className="analysis-card">
    <div className="result-heading"><span className="eyebrow">VERIFIED ANALYSIS</span><span className="muted">Knowledge v{result.version} · {result.as_of}</span></div>
    <h3>{title}</h3>
    <p>{result.explanation}</p>
    {!!Object.keys(result.metrics).length && <div className="metrics">{Object.entries(result.metrics).map(([key, value]) => <div key={key}><strong>{value.toLocaleString()}</strong><span>{key.replaceAll("_", " ")}</span></div>)}</div>}
    {activeKind === "summary" ? null : activeKind === "card" ? <div className="status-grid">{rows.map((r, i) => <article key={i}><span className={`pill ${r.producing ? "green" : "amber"}`}>{r.producing ? "Producing" : "Not producing"}</span><h3>{String(r.asset_id)}</h3><div className="temperature">{Number(r.delta_c).toFixed(1)}<small>°C above ambient</small></div><p>Last inspected {String(r.inspected_at).slice(0, 10)} · {String(r.method || "unknown method")} · {String(r.inspector_name || r.inspector_user_id || "unknown inspector")}</p><p>Type threshold: {String(r.threshold_c)}°C</p><p>{String(r.reason || r.notes || "No recorded reason")}</p>{Array.isArray(r.context) && r.context.map((n: {note_id: string; text: string}) => <p key={n.note_id}>{n.text}</p>)}</article>)}</div> : activeKind === "temperature-cards" ? <div className="temperature-cards">{rows.map((r, i) => <article key={i}><div><strong>{String(r.asset_id)}</strong><time>{new Date(String(r.inspected_at)).toLocaleDateString()}</time></div><div className="temperature-values"><span><b>{Number(r.wellhead_temp_c).toFixed(1)}°</b><small>Wellhead</small></span><span><b>{Number(r.ambient_temp_c).toFixed(1)}°</b><small>Ambient</small></span><span><b>{Number(r.delta_c).toFixed(1)}°</b><small>Difference</small></span></div><span className={`pill ${r.producing ? "green" : "amber"}`}>{r.producing ? "Producing" : "Not producing"}</span></article>)}</div> : activeKind === "table" ? <><input className="filter" value={filter} onChange={e => setFilter(e.target.value)} placeholder="Filter these results…" aria-label="Filter results" /><div className="table-scroll"><table><thead><tr>{columns.map(c => <th key={c}>{columnLabel(c)}</th>)}</tr></thead><tbody>{rows.map((r,i) => <tr key={i}>{columns.map(c => <td key={c}>{displayCell(c, r[c])}</td>)}</tr>)}</tbody></table></div></> : <><Plot result={result} kind={activeKind} />{activeKind === "map" && <p className="muted">Geographic coordinate view · longitude / latitude · no external tile requests</p>}{result.operation === "predictive_maintenance" && <div className="priority-list">{rows.map((r, i) => <article key={i}><div><span className={`priority priority-${String(r.priority)}`}>{String(r.priority)}</span><strong>{String(r.asset_id)}</strong><b>{String(r.priority_score)}</b></div><p>{String(r.signals)}</p><small>{String(r.recommended_action)}</small></article>)}</div>}</>}
    {!rows.length && <p className="empty">No matching observations in this scope.</p>}
    <p className="muted">{result.rows.length} displayed / {result.total_rows} matching rows · charts use displayed rows</p>
    <details><summary>Sources, rules & limitations <span>{result.evidence_ids.length} records</span></summary><ul>{result.warnings.map(w => <li key={w}>{w}</li>)}</ul><div className="source-chips">{result.evidence_ids.map(id => <button key={id} onClick={() => inspect(id)}>{id}</button>)}</div>{result.rules.map(r => <button className="document-link" key={r.path} onClick={async () => {try {setEvidence(await api(`document?path=${encodeURIComponent(r.path)}`));} catch(e) {setError(String(e));}}}>{r.path}</button>)}</details>
    {error && <p role="alert" className="error">{error}</p>}
    {evidence !== null && <div className="evidence-panel"><div><strong>Source evidence</strong><button onClick={() => setEvidence(null)}>Close</button></div><pre>{JSON.stringify(evidence,null,2)}</pre></div>}
  </section>;
}
