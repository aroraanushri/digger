"use client";
import { useState } from "react";
import { api } from "./AnalysisView";

export function FieldUpdate({userId, version, onSaved, onClose}: {userId: string; version: number; onSaved: () => void; onClose: () => void}) {
  const [kind, setKind] = useState("inspection");
  const [asset, setAsset] = useState("WELL-0164");
  const [target, setTarget] = useState("INS-0164-0205");
  const [field, setField] = useState("wellhead_temp_c");
  const [value, setValue] = useState("41");
  const [ambient, setAmbient] = useState("20");
  const [when, setWhen] = useState("2026-10-16T12:00");
  const [reason, setReason] = useState("");
  const [review, setReview] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [requestId] = useState(() => crypto.randomUUID());
  async function save() {
    setPending(true); setError("");
    try {
      const payload = {request_id: requestId, user_id: userId, asset_id: asset, expected_version: version, kind, reason, ...(kind === "inspection" ? {inspection: {inspected_at: when + ":00Z", wellhead_temp_c: Number(value), ambient_temp_c: Number(ambient), notes: reason}} : kind === "correction" ? {target_id: target, field, value: field === "spill_detected" ? value === "true" : Number(value)} : {})};
      await api("knowledge", {method: "POST", body: JSON.stringify(payload)});
      onSaved();
    } catch(e) {setError(String(e));} finally {setPending(false);}
  }
  return <div className="modal-backdrop"><section className="field-modal" role="dialog" aria-modal="true" aria-labelledby="field-title"><div className="modal-header"><span className="eyebrow">FIELD KNOWLEDGE</span><button onClick={onClose} aria-label="Close field update">✕</button></div><h2 id="field-title">Bring the field into focus.</h2><p>Add an observation or correct a historical record. Original evidence is preserved.</p><form onSubmit={e => {e.preventDefault(); setReview(true);}}>
    <label>Update type<select value={kind} onChange={e => {setKind(e.target.value); setReview(false);}}><option value="inspection">New inspection</option><option value="correction">Correct an inspection</option><option value="note">Operational note</option></select></label>
    <label>Well ID<input required pattern="WELL-[0-9]+" value={asset} onChange={e => {setAsset(e.target.value); setReview(false);}} /></label>
    {kind === "inspection" && <><label>Observed at (UTC)<input required type="datetime-local" value={when} onChange={e => {setWhen(e.target.value); setReview(false);}} /></label><label>Ambient temperature (°C)<input required type="number" step="0.1" min="-80" max="200" value={ambient} onChange={e => {setAmbient(e.target.value); setReview(false);}} /></label></>}
    {kind === "correction" && <><label>Inspection ID<input required value={target} onChange={e => {setTarget(e.target.value); setReview(false);}} /></label><label>Field<select value={field} onChange={e => {setField(e.target.value); setValue(e.target.value === "spill_detected" ? "false" : "41"); setReview(false);}}><option value="wellhead_temp_c">Wellhead temperature</option><option value="ambient_temp_c">Ambient temperature</option><option value="spill_est_volume_bbl">Spill volume (barrels)</option><option value="spill_detected">Spill detection</option></select></label></>}
    {kind !== "note" && <label>{kind === "inspection" ? "Wellhead temperature (°C)" : "Corrected value"}{kind === "correction" && field === "spill_detected" ? <select value={value} onChange={e => {setValue(e.target.value); setReview(false);}}><option value="false">Not a spill</option><option value="true">Spill detected</option></select> : <input required type="number" step="0.1" value={value} onChange={e => {setValue(e.target.value); setReview(false);}} />}</label>}
    <label>{kind === "note" ? "Field note" : "Observation / reason for change"}<textarea required minLength={3} maxLength={4000} rows={3} value={reason} onChange={e => {setReason(e.target.value); setReview(false);}} /></label>
    <p className="muted">Author: {userId} · Based on knowledge v{version}. New inspections after the selected analysis date require advancing that date.</p>
    {review ? <div className="review"><strong>Review update</strong><p>{kind} · {asset}{kind === "correction" ? ` · ${target} · ${field} → ${value}` : ""}</p><p>{reason}</p><button className="primary" type="button" disabled={pending} onClick={save}>{pending ? "Saving…" : "Save shared field update"}</button></div> : <button className="primary" type="submit">Review update →</button>}
    {error && <p role="alert" className="error">{error}</p>}
  </form></section></div>;
}
