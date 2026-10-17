import json
import re
from datetime import date, datetime, timedelta

from .models import QueryPlan
from .store import Store


def _report_text(store: Store, repair: dict) -> tuple[str, str | None]:
    report_file = repair.get('report_file')
    if not report_file:
        return '', None
    path = store.source / 'assets' / repair['asset_id'] / report_file
    return (path.read_text(encoding='utf-8'), path.relative_to(store.source).as_posix()) if path.is_file() else ('', None)


def _containment_hours(text: str) -> float | None:
    match = re.search(r"contained about\s+(\d+(?:\.\d+)?)\s+hours", text, re.I)
    return float(match.group(1)) if match else None


def analyze(store: Store, plan: QueryPlan):
    with store.lock:
        if getattr(store, "projection_failed", False):
            raise RuntimeError("An update is saved but its analytical projection needs a restart.")
        unknown = set(plan.asset_ids) - set(store.assets)
        if unknown:
            raise ValueError(f"Unknown assets: {', '.join(sorted(unknown))}")
        areas = store.area_records
        area_names = {a['area_id']: a['name'] for a in areas}
        selected_area = None
        if plan.area:
            matches = [k for k, v in area_names.items() if plan.area.casefold() in (k.casefold(), v.casefold())]
            if not matches:
                raise ValueError("Unknown area; use a supplied area ID or full name")
            selected_area = matches[0]
        filters, args = ["CAST(inspected_at AS DATE) <= ?"], [plan.end]
        if plan.operation in {"predictive_maintenance", "compliance", "rca"}:
            effective_start = plan.end - timedelta(days=365)
        elif plan.operation in {"containment", "reportable_spills"}:
            effective_start = plan.start or plan.end - timedelta(days=90)
        else:
            effective_start = plan.start
        if effective_start:
            filters.append("CAST(inspected_at AS DATE) >= ?")
            args.append(effective_start)
        if plan.asset_ids:
            filters.append("asset_id IN (SELECT unnest(?))")
            args.append(plan.asset_ids)
        if selected_area:
            filters.append("area_id=?")
            args.append(selected_area)
        if plan.team_id:
            filters.append("team_id=?")
            args.append(plan.team_id)
        base = "SELECT * FROM effective WHERE " + " AND ".join(filters)
        evidence = []
        extra_rule_paths = []
        warnings = ["Synthetic challenge data. Operational rules are fictional."]
        explanation = ""
        metrics = {}

        def query(sql, params=args):
            cursor = store.db.execute(sql, params)
            cols = [c[0] for c in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]

        if plan.operation == "status":
            rows = query(f"SELECT * FROM ({base}) QUALIFY ROW_NUMBER() OVER(PARTITION BY asset_id ORDER BY inspected_at DESC, inspection_id DESC)=1 ORDER BY asset_id")
            if plan.only_nonproducing:
                rows = [r for r in rows if not r['producing']]
            metrics = {"matching_wells": len(rows)}
            explanation = "Production is derived from wellhead minus ambient temperature using the well-type manual. This is the latest observation on or before the analysis date, not continuous live telemetry."
            notes = store.records('context')
            repairs = store.records('maintenance')
            for row in rows:
                asset = store.assets[row['asset_id']]
                row['recorded_status'] = asset.get('recorded_status', asset.get('status', 'unknown'))
                row['well_status'] = asset.get('status', 'active')
                row['decommissioned_on'] = asset.get('decommissioned_on')
                row['inspector_name'] = store.users.get(row.get('inspector_user_id'), {}).get('name', row.get('inspector_user_id'))
                row['context'] = [n for n in notes if n['asset_id'] == row['asset_id'] and n['valid_from'] <= plan.end.isoformat() and (not n.get('valid_to') or n['valid_to'] >= plan.end.isoformat())]
                updates = [{'version': e['version'], **json.loads(e['payload'])} for e in store.sql.execute('SELECT version,payload FROM events') if json.loads(e['payload']).get('asset_id') == row['asset_id']]
                row['updates'] = updates
                corrections = [event for event in updates if event['kind'] == 'correction' and event.get('target_id') == row['inspection_id']]
                row['corrections_applied'] = [{
                    'update_id': f"UPDATE-{event['version']}",
                    'field': event['field'],
                    'value': event['value'],
                    'reason': event['reason'],
                    'author': store.users.get(event['user_id'], {}).get('name', event['user_id']),
                } for event in corrections]
                evidence.extend(item['update_id'] for item in row['corrections_applied'])
                row['context'].extend({'note_id': f"UPDATE-{event['version']}", 'note_type': event['kind'], 'text': event['reason'], 'author_user_id': event['user_id'], 'valid_from': event.get('valid_from') or event.get('inspection', {}).get('inspected_at', plan.end.isoformat())[:10], 'valid_to': event.get('valid_to'), 'confirmation_status': event.get('confirmation_status')} for event in updates if event['kind'] == 'note' and (not event.get('valid_from') or event['valid_from'] <= plan.end.isoformat()) and (not event.get('valid_to') or event['valid_to'] >= plan.end.isoformat()))
                open_repairs = [r for r in repairs if r['asset_id'] == row['asset_id'] and r['started_at'][:10] <= plan.end.isoformat() and (not r.get('completed_at') or r['completed_at'][:10] > plan.end.isoformat())]
                row['open_repairs'] = [{'maintenance_id': r['maintenance_id'], 'equipment_kind': r['equipment_kind'], 'issue': r['issue'], 'started_at': r['started_at']} for r in open_repairs]
                if open_repairs:
                    repair = open_repairs[-1]
                    row['reason'] = f"Open repair {repair['maintenance_id']}: {repair['equipment_kind']} — {repair['issue']} (started {repair['started_at'][:10]})."
                    evidence.append(repair['maintenance_id'])
                elif row['context']:
                    row['reason'] = row['context'][-1]['text']
                elif row.get('notes'):
                    row['reason'] = f"No recorded reason. Inspector noted: {row['notes']}"
                elif not row['producing']:
                    row['reason'] = "No recorded reason; field check recommended."
                else:
                    row['reason'] = "No exception recorded."
                for n in row['context']:
                    evidence.append(n['note_id'])
                if row['recorded_status'] == 'active' and not row['producing']:
                    warnings.append(f"{row['asset_id']}: recorded active status disagrees with latest inspection.")
                if row['producing'] and corrections and re.search(r"not moving|shut|offline", row.get('notes') or '', re.I):
                    warnings.append(f"{row['asset_id']}: corrected temperature indicates production, but the inspection note says: {row['notes']}")
        elif plan.operation == "status_disagreements":
            latest_rows = query(f"SELECT * FROM ({base}) QUALIFY ROW_NUMBER() OVER(PARTITION BY asset_id ORDER BY inspected_at DESC, inspection_id DESC)=1 ORDER BY asset_id")
            notes = [note for note in store.records('context') if note.get('note_type') == 'operator_note' and note.get('created_at', '')[:10] <= plan.end.isoformat()]
            rows = []
            producing_note = re.compile(r"\b(?:producing|pumping normally|running(?: fine| normally)?|back online|unit still running|level normal)\b", re.I)
            stopped_note = re.compile(r"\b(?:not producing|shut[- ]in|offline|stopped|stays shut|not running|restart pushed back|waiting on .*parts)\b", re.I)
            for inspection in latest_rows:
                candidates = [note for note in notes if note['asset_id'] == inspection['asset_id']]
                if not candidates:
                    continue
                latest_note = max(candidates, key=lambda note: (note.get('created_at', ''), note['note_id']))
                note_text = latest_note['text']
                note_producing = True if producing_note.search(note_text) and not stopped_note.search(note_text) else False if stopped_note.search(note_text) else None
                if note_producing is None or note_producing == inspection['producing']:
                    continue
                inspection_status = 'Producing oil' if inspection['producing'] else 'Not producing oil'
                rows.append({'asset_id': inspection['asset_id'], 'area_id': inspection['area_id'], 'latest_inspection_id': inspection['inspection_id'], 'latest_inspection_date': inspection['inspected_at'], 'latest_inspection_status': inspection_status, 'latest_operator_note_date': latest_note['created_at'], 'latest_operator_note': note_text, 'status_now': inspection_status, 'status_basis': 'Latest inspection; operator note disagrees', 'evidence_ids': [inspection['inspection_id'], latest_note['note_id']]})
                evidence.extend([inspection['inspection_id'], latest_note['note_id']])
            metrics = {'disagreements': len(rows), 'producing_by_inspection': sum(row['status_now'] == 'Producing oil' for row in rows), 'not_producing_by_inspection': sum(row['status_now'] == 'Not producing oil' for row in rows)}
            explanation = "Only wells with an explicit producing/not-producing operator note that conflicts with the latest inspection are shown. Status now follows the latest inspection's temperature rule; the note and both record IDs remain available as evidence."
        elif plan.operation == "temperatures":
            rows = query(f"SELECT * FROM ({base}) ORDER BY inspected_at,asset_id")
            explanation = "Observed temperatures and their difference. Production uses a type-specific threshold; missing inspections are not interpolated."
        elif plan.operation == "regional_temperatures":
            all_records = query(base)
            evidence.extend(record['inspection_id'] for record in all_records)
            rows = query(f"""
                SELECT area_id,
                    AVG(wellhead_temp_c) AS avg_wellhead_temp_c,
                    AVG(ambient_temp_c) AS avg_ambient_temp_c,
                    AVG(delta_c) AS avg_delta_c,
                    MIN(delta_c) AS min_delta_c,
                    MAX(delta_c) AS max_delta_c,
                    STDDEV_SAMP(delta_c) AS delta_stddev_c,
                    COUNT(*) AS observations,
                    COUNT(DISTINCT asset_id) AS wells,
                    AVG(CASE WHEN producing THEN 1 ELSE 0 END) * 100 AS producing_pct
                FROM ({base})
                GROUP BY area_id
                ORDER BY avg_delta_c DESC, area_id
            """)
            metrics = {
                'hottest_region': area_names[rows[0]['area_id']] if rows else 'No observations',
                'hottest_avg_delta_c': round(rows[0]['avg_delta_c'], 2) if rows else 0,
                'regions_compared': len(rows),
            }
            explanation = "Regions are ranked by the average wellhead-to-ambient temperature difference across all available inspections in the selected period. A larger average indicates a hotter operating signal; the spread and observation counts show whether that comparison is consistent and adequately observed."
        elif plan.operation == "hottest_wells":
            all_records = query(base)
            evidence.extend(record['inspection_id'] for record in all_records)
            rows = query(f"""
                SELECT asset_id, area_id,
                    AVG(wellhead_temp_c) AS avg_wellhead_temp_c,
                    AVG(ambient_temp_c) AS avg_ambient_temp_c,
                    AVG(delta_c) AS avg_delta_c,
                    MAX(delta_c) AS max_delta_c,
                    COUNT(*) AS observations,
                    COUNT(DISTINCT CAST(inspected_at AS DATE)) AS observed_days
                FROM ({base})
                GROUP BY asset_id, area_id
                ORDER BY avg_delta_c DESC, asset_id
            """)
            metrics = {
                'hottest_well': rows[0]['asset_id'] if rows else 'No observations',
                'hottest_avg_delta_c': round(rows[0]['avg_delta_c'], 2) if rows else 0,
                'wells_compared': len(rows),
            }
            explanation = "Wells are ranked by their average wellhead-to-ambient temperature difference over the selected period. The ranking uses observed inspections only, so it identifies the hottest observed wells rather than estimating unobserved temperatures."
        elif plan.operation == "downtime":
            all_records = query(base)
            evidence += [r['inspection_id'] for r in all_records if not r['producing']]
            rows = query(f"SELECT asset_id,area_id,count(*) FILTER(WHERE NOT producing)*7 AS downtime_days,count(*) AS observed_inspections FROM ({base}) GROUP BY asset_id,area_id ORDER BY downtime_days DESC,asset_id")
            metrics = {"downtime_days": sum(r['downtime_days'] for r in rows)}
            explanation = "Downtime = seven days × observed non-producing inspections. Missed inspections add zero; repair downtime_hours is not used. Equal values are ordered by asset ID."
        elif plan.operation == "spills":
            rows = query(f"SELECT * FROM ({base}) WHERE spill_detected ORDER BY inspected_at DESC,asset_id")
            metrics = {"spill_count": len(rows), "volume_bbl": round(sum(r['spill_est_volume_bbl'] or 0 for r in rows), 3), "unknown_volumes": sum(r['spill_est_volume_bbl'] is None for r in rows)}
            explanation = "Counts include effective spill detections after formal corrections. Unknown volumes are excluded from the known-volume total."
        elif plan.operation == 'compliance':
            spills = query(f"SELECT * FROM ({base}) WHERE spill_detected AND spill_est_volume_bbl >= 5 ORDER BY inspected_at")
            regulatory = store.records('regulatory-events')
            maintenance = store.records('maintenance')
            rows = []
            for spill in spills:
                inspection_id = spill['inspection_id']
                related_events = [event for event in regulatory if event.get('related_inspection_id') == inspection_id]
                report = next((event for event in related_events if event['event_type'] == 'spill_report_filed'), None)
                report_limit = 2 if spill['spill_est_volume_bbl'] >= 25 else 24
                due = spill['inspected_at'].date() + timedelta(hours=report_limit)
                if (report and report.get('hours_to_report') is not None and report['hours_to_report'] > report_limit) or (not report and due < plan.end):
                    ids = [inspection_id] + [event['event_id'] for event in related_events if event['protocol_id'] == 'SR-01']
                    rows.append({'asset_id': spill['asset_id'], 'protocol': 'SR-01', 'status': 'breach', 'detail': f"Written notice was {'filed after ' + str(report['hours_to_report']) + ' hours' if report else 'not recorded'}; limit {report_limit} hours.", 'due': due, 'fine_usd': sum(event.get('fine_usd', 0) for event in related_events if event.get('status') == 'open'), 'evidence_ids': ids})
                    evidence.extend(ids)
                linked_repairs = [repair for repair in maintenance if repair.get('triggered_by_inspection_id') == inspection_id]
                for repair in linked_repairs:
                    text, report_path = _report_text(store, repair)
                    hours = _containment_hours(text)
                    if hours is not None and hours > 4:
                        ids = [inspection_id, repair['maintenance_id']]
                        rows.append({'asset_id': spill['asset_id'], 'protocol': 'SR-02', 'status': 'breach', 'detail': f"Contained after about {hours:g} hours; limit 4 hours.", 'due': spill['inspected_at'].date(), 'fine_usd': 0, 'evidence_ids': ids, 'report_path': report_path})
                        evidence.extend(ids)
                verification_due = spill['inspected_at'].date() + timedelta(days=60)
                verification = [event for event in related_events if event['protocol_id'] == 'SR-03']
                verified = any(event.get('status') == 'closed' for event in verification)
                if verification_due < plan.end and not verified:
                    ids = [inspection_id] + [event['event_id'] for event in verification]
                    rows.append({'asset_id': spill['asset_id'], 'protocol': 'SR-03', 'status': 'breach', 'detail': f"Cleanup verification was due {verification_due.isoformat()} and remains open.", 'due': verification_due, 'fine_usd': 0, 'evidence_ids': ids})
                    evidence.extend(ids)
            selected_assets = set(plan.asset_ids) if plan.asset_ids else {asset_id for asset_id, asset in store.assets.items() if not selected_area or asset['area_id'] == selected_area}
            notices = [event for event in regulatory if event['asset_id'] in selected_assets and event['event_type'] == 'notice_of_violation' and event['status'] == 'open' and (not effective_start or event['date'] >= effective_start.isoformat()) and event['date'] <= plan.end.isoformat()]
            for notice in notices:
                due = date.fromisoformat(notice['date']) + timedelta(days=30)
                responses = [event for event in regulatory if event['asset_id'] == notice['asset_id'] and event['event_type'] == 'nov_response_filed' and notice['date'] <= event['date'] <= due.isoformat()]
                if due < plan.end and not responses:
                    rows.append({'asset_id': notice['asset_id'], 'protocol': 'RC-04', 'status': 'breach', 'detail': f"Open notice {notice['event_id']} had no recorded response by {due.isoformat()}.", 'due': due, 'fine_usd': notice.get('fine_usd', 0), 'evidence_ids': [notice['event_id']]})
                    evidence.append(notice['event_id'])
            corrective = [repair for repair in maintenance if repair['asset_id'] in selected_assets and repair['maintenance_type'] == 'corrective' and effective_start.isoformat() <= repair['started_at'][:10] <= plan.end.isoformat()]
            grouped = {}
            for repair in corrective:
                grouped.setdefault((repair['asset_id'], repair['equipment_kind']), []).append(repair)
            for (asset_id, equipment), repairs in grouped.items():
                if len(repairs) >= 3:
                    ids = [repair['maintenance_id'] for repair in repairs]
                    rows.append({'asset_id': asset_id, 'protocol': 'Manual §10', 'status': 'pending', 'detail': f"Root-cause analysis is required for {equipment}: {len(repairs)} corrective repairs in 12 months.", 'due': None, 'fine_usd': 0, 'evidence_ids': ids})
                    evidence.extend(ids)
            source_notes = [note for note in store.records('context') if note['asset_id'] in selected_assets and 'not yet in the maintenance system' in note['text'].casefold() and note['valid_from'] <= plan.end.isoformat()]
            for note in source_notes:
                rows.append({'asset_id': note['asset_id'], 'protocol': 'Manual records', 'status': 'pending', 'detail': note['text'], 'due': None, 'fine_usd': 0, 'evidence_ids': [note['note_id']]})
                evidence.append(note['note_id'])
            annual_tests = [repair for repair in maintenance if repair['asset_id'] in selected_assets and repair['issue'] == 'Annual flowline integrity test' and repair['started_at'][:4] == str(plan.end.year)]
            for repair in annual_tests:
                rows.append({'asset_id': repair['asset_id'], 'protocol': 'RC-02', 'status': 'met', 'detail': f"Annual flowline integrity test completed {repair['started_at'][:10]}.", 'due': None, 'fine_usd': 0, 'evidence_ids': [repair['maintenance_id']]})
                evidence.append(repair['maintenance_id'])
            rows.sort(key=lambda row: ({'breach': 0, 'pending': 1, 'met': 2}[row['status']], row['protocol'], row['asset_id']))
            metrics = {'breaches': sum(row['status'] == 'breach' for row in rows), 'open_fines_usd': sum(event.get('fine_usd', 0) for event in regulatory if event['asset_id'] in selected_assets and event.get('status') == 'open')}
            explanation = "Compliance results combine effective inspections, linked repairs and reports, regulatory events, well-type manuals, and SOP deadlines. Pending actions are shown separately from confirmed breaches."
            extra_rule_paths.extend(['fleet/sops/spill-response-sop.md', 'fleet/sops/regulatory-compliance-sop.md'])
        elif plan.operation == 'containment':
            spills = query(f"SELECT * FROM ({base}) WHERE spill_detected ORDER BY inspected_at,asset_id")
            maintenance = store.records('maintenance')
            rows = []
            for spill in spills:
                for repair in maintenance:
                    if repair.get('triggered_by_inspection_id') != spill['inspection_id']:
                        continue
                    text, report_path = _report_text(store, repair)
                    hours = _containment_hours(text)
                    if hours is not None and hours > 4:
                        ids = [spill['inspection_id'], repair['maintenance_id']]
                        rows.append({'asset_id': spill['asset_id'], 'inspection_id': spill['inspection_id'], 'inspected_at': spill['inspected_at'], 'spill_est_volume_bbl': spill['spill_est_volume_bbl'], 'containment_hours': hours, 'limit_hours': 4, 'maintenance_id': repair['maintenance_id'], 'report_path': report_path, 'evidence_ids': ids})
                        evidence.extend(ids)
            metrics = {'late_containment_spills': len(rows)}
            explanation = "Containment duration is extracted from each linked maintenance report and compared with the four-hour SR-02 limit."
            extra_rule_paths.append('fleet/sops/spill-response-sop.md')
        elif plan.operation == 'reportable_spills':
            spills = query(f"SELECT * FROM ({base}) WHERE spill_detected AND spill_est_volume_bbl >= 5 ORDER BY inspected_at,asset_id")
            regulatory = store.records('regulatory-events')
            rows = []
            for spill in spills:
                events = [event for event in regulatory if event.get('related_inspection_id') == spill['inspection_id'] and event['protocol_id'] == 'SR-01']
                limit = 2 if spill['spill_est_volume_bbl'] >= 25 else 24
                due_at = spill['inspected_at'] + timedelta(hours=limit)
                filed = next((event for event in events if event['event_type'] == 'spill_report_filed'), None)
                status = 'filed' if filed and filed.get('hours_to_report', limit + 1) <= limit else 'late' if filed else 'overdue' if due_at.date() <= plan.end else 'due'
                ids = [spill['inspection_id']] + [event['event_id'] for event in events]
                rows.append({'asset_id': spill['asset_id'], 'inspection_id': spill['inspection_id'], 'inspected_at': spill['inspected_at'], 'spill_est_volume_bbl': spill['spill_est_volume_bbl'], 'report_due_at': due_at, 'report_status': status, 'evidence_ids': ids})
                evidence.extend(ids)
            metrics = {'reportable_spills': len(rows), 'missing_or_late_notices': sum(row['report_status'] != 'filed' for row in rows)}
            explanation = "Spills at or above five barrels require an SR-01 written notice within 24 hours; major spills use the two-hour limit. Corrected effective volumes determine reporting duty."
            extra_rule_paths.append('fleet/sops/spill-response-sop.md')
        elif plan.operation == 'rca':
            selected_assets = [asset_id for asset_id, asset in store.assets.items() if (not plan.asset_ids or asset_id in plan.asset_ids) and (not selected_area or asset['area_id'] == selected_area)]
            equipment = (plan.equipment_kind or 'pump_jack').casefold().replace(' ', '_')
            repairs = [repair for repair in store.records('maintenance') if repair['asset_id'] in selected_assets and repair['maintenance_type'] == 'corrective' and repair['equipment_kind'].casefold() == equipment and effective_start.isoformat() <= repair['started_at'][:10] <= plan.end.isoformat()]
            policies = [{'version': event['version'], **json.loads(event['payload'])} for event in store.sql.execute('SELECT version,payload FROM events ORDER BY version') if json.loads(event['payload']).get('kind') == 'policy']
            rows = []
            for asset_id in selected_assets:
                matching = [repair for repair in repairs if repair['asset_id'] == asset_id]
                asset = store.assets[asset_id]
                current_type = next((period['well_type'] for period in asset.get('type_history', []) if period['from'] <= plan.end.isoformat() and (not period.get('to') or plan.end.isoformat() <= period['to'])), asset['well_type'])
                manual_threshold = 2 if current_type == 'T500' and equipment == 'esp_pump' else 3
                override = next((policy for policy in reversed(policies) if policy.get('scope_well_type') == current_type and policy.get('equipment_kind', '').casefold().replace(' ', '_') == equipment), None)
                threshold = override['corrective_threshold'] if override else manual_threshold
                if len(matching) >= threshold:
                    ids = [repair['maintenance_id'] for repair in matching]
                    if override:
                        ids.append(f"UPDATE-{override['version']}")
                    rows.append({'asset_id': asset_id, 'well_type': current_type, 'equipment_kind': equipment, 'corrective_repairs_12m': len(matching), 'threshold': threshold, 'rule_source': f"User policy by {store.users[override['user_id']]['name']}" if override else f'{current_type} manual', 'evidence_ids': ids})
                    evidence.extend(ids)
            rows.sort(key=lambda row: (-row['corrective_repairs_12m'], row['asset_id']))
            metrics = {'wells_requiring_rca': len(rows)}
            explanation = "RCA is required when corrective repairs on the same equipment meet the applicable 12-month threshold. Scoped user policy overrides are shown with their author; otherwise the manual threshold is three."
        elif plan.operation == 'planned_downtime':
            selected_assets = set(plan.asset_ids) if plan.asset_ids else set(store.assets)
            rows = []
            for note in store.records('context'):
                if note['asset_id'] in selected_assets and re.search(r"planned|workover|shut in", note['text'], re.I) and (not note.get('valid_to') or note['valid_to'] >= plan.end.isoformat()):
                    rows.append({'asset_id': note['asset_id'], 'starts': note['valid_from'], 'ends': note.get('valid_to'), 'status': 'recorded', 'detail': note['text'], 'author': store.users.get(note['author_user_id'], {}).get('name'), 'evidence_ids': [note['note_id']]})
                    evidence.append(note['note_id'])
            for event in store.sql.execute('SELECT version,payload FROM events ORDER BY version'):
                payload = json.loads(event['payload'])
                if payload.get('kind') == 'note' and payload.get('asset_id') in selected_assets and re.search(r"planned|workover|shut in", payload['reason'], re.I):
                    ids = [f"UPDATE-{event['version']}"]
                    rows.append({'asset_id': payload['asset_id'], 'starts': payload.get('valid_from'), 'ends': payload.get('valid_to'), 'status': payload.get('confirmation_status') or 'recorded', 'detail': payload['reason'], 'author': store.users.get(payload['user_id'], {}).get('name'), 'evidence_ids': ids})
                    evidence.extend(ids)
            metrics = {'planned_downtime_records': len(rows)}
            explanation = "Planned downtime comes from dated field notes and preserves the author and confirmation status."
        elif plan.operation == 'spill_review':
            candidates = query(f"SELECT * FROM ({base}) ORDER BY inspected_at,asset_id")
            inspection = next((row for row in candidates if row['spill_detected']), candidates[0] if candidates else None)
            if not inspection:
                rows = []
            else:
                notes = [note for note in store.records('context') if note['asset_id'] == inspection['asset_id'] and inspection['inspection_id'] in note['text']]
                repairs = [repair for repair in store.records('maintenance') if repair.get('triggered_by_inspection_id') == inspection['inspection_id']]
                ids = [inspection['inspection_id']] + [note['note_id'] for note in notes] + [repair['maintenance_id'] for repair in repairs]
                evidence.extend(ids)
                rows = [{'asset_id': inspection['asset_id'], 'inspection_id': inspection['inspection_id'], 'inspected_at': inspection['inspected_at'], 'spill_est_volume_bbl': inspection['spill_est_volume_bbl'], 'inspection_note': inspection['notes'], 'conclusion': 'Detection stands as crude oil' if inspection['spill_detected'] else 'Detection was formally corrected', 'disagreement': [f"{store.users.get(note['author_user_id'], {}).get('name', note['author_user_id'])}: {note['text']}" for note in notes], 'linked_repairs': [repair['maintenance_id'] for repair in repairs], 'evidence_ids': ids}]
            metrics = {'detections_reviewed': len(rows)}
            explanation = "The effective inspection determines whether the detection stands. Conflicting human notes remain visible; only a formal correction changes the spill record, and linked repair work is supporting evidence."
        elif plan.operation == 'gas_response':
            start_day = plan.start or date(2026, 11, 1)
            end_day = plan.end
            odor_pattern = re.compile(r"gas\s+(?:smell|odor|odour)|smell(?:ed)?\s+(?:of\s+)?gas|rotten[- ]egg|H2S\s+(?:monitor\s+)?alarm", re.I)
            reports = [
                note for note in store.records('context')
                if note.get('note_type') == 'operator_note'
                and odor_pattern.search(note.get('text', ''))
                and start_day.isoformat() <= note['created_at'][:10] <= end_day.isoformat()
                and (not plan.asset_ids or note['asset_id'] in plan.asset_ids)
                and (not selected_area or store.assets[note['asset_id']]['area_id'] == selected_area)
            ]
            repairs_by_asset = {}
            for repair in store.records('maintenance'):
                if repair['started_at'][:10] <= end_day.isoformat():
                    repairs_by_asset.setdefault(repair['asset_id'], []).append((repair['started_at'], repair['maintenance_id']))
            inspections_by_asset = {}
            for inspection in store.effective.values():
                if inspection['inspected_at'][:10] <= end_day.isoformat() and inspection.get('method') == 'manual':
                    inspections_by_asset.setdefault(inspection['asset_id'], []).append((inspection['inspected_at'], inspection['inspection_id']))

            rows = []
            for note in sorted(reports, key=lambda item: (item['created_at'], item['asset_id'], item['note_id'])):
                reported_at = datetime.fromisoformat(note['created_at'].replace('Z', '+00:00'))
                deadline_hours = 12 if reported_at.date() >= date(2027, 1, 1) else 24
                response_candidates = []
                for timestamp, record_id in repairs_by_asset.get(note['asset_id'], []):
                    response_at = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    if response_at > reported_at:
                        response_candidates.append((response_at, record_id))
                for timestamp, record_id in inspections_by_asset.get(note['asset_id'], []):
                    response_at = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    if response_at > reported_at:
                        response_candidates.append((response_at, record_id))
                response_at, response_id = min(response_candidates, default=(None, None), key=lambda item: item[0] or datetime.max.replace(tzinfo=reported_at.tzinfo))
                hours = round((response_at - reported_at).total_seconds() / 3600, 2) if response_at else None
                met = hours is not None and hours <= deadline_hours
                area_id = store.assets[note['asset_id']]['area_id']
                ids = [note['note_id']] + ([response_id] if response_id else [])
                evidence.extend(ids)
                rows.append({
                    'asset_id': note['asset_id'], 'area_id': area_id, 'reported_at': reported_at,
                    'response_id': response_id, 'response_at': response_at, 'hours_to_response': hours,
                    'deadline_hours': deadline_hours, 'result': 'Met' if met else 'Missed',
                    'note_text': note['text'], 'evidence_ids': ids,
                })
            metrics = {'gas_reports': len(rows), 'met_deadline': sum(row['result'] == 'Met' for row in rows), 'missed_deadline': sum(row['result'] == 'Missed' for row in rows)}
            explanation = "OPS-1 gas odor reports are matched to the first later maintenance start or manual inspection on the same well. Drone inspections do not count. The deadline is 24 hours before 1 Jan 2027 and 12 hours from that date."
            extra_rule_paths.append('fleet/sops/operations-sop-rev2.md')
        elif plan.operation == 'bypasses':
            sop_dir = store.source / 'fleet' / 'sops'
            memo_pattern = re.compile(r'MOC-\d{4}-\d{3}', re.I)
            memos = []
            for path in sorted(sop_dir.glob('moc-*.md')):
                text = path.read_text(encoding='utf-8')
                memo_id = path.stem.upper()
                date_match = re.search(r'(?:Effective from|\*\*Date:\*\*)\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4})', text, re.I)
                expiry_match = re.search(r'expires on\s+(\d{1,2}\s+[A-Za-z]+\s+\d{4})', text, re.I)
                if not date_match or not expiry_match:
                    continue
                wells = set(re.findall(r'\bWELL-\d{4}\b', text))
                extension = re.search(r'extends\s+(MOC-\d{4}-\d{3})', text, re.I)
                memos.append({
                    'memo_id': memo_id, 'path': path.relative_to(store.source).as_posix(),
                    'wells': wells, 'effective': datetime.strptime(date_match.group(1), '%d %b %Y').date(),
                    'expires': datetime.strptime(expiry_match.group(1), '%d %b %Y').date(),
                    'extends': extension.group(1).upper() if extension else None,
                })
            for extension in (memo for memo in memos if memo['extends']):
                original = next((memo for memo in memos if memo['memo_id'] == extension['extends']), None)
                if original:
                    original['expires'] = extension['expires']
                    original['current_memo_id'] = extension['memo_id']
                    original['extension_path'] = extension['path']

            notes = [note for note in store.records('context') if note.get('note_type') == 'operator_note']
            bypass_events = {}
            start_pattern = re.compile(r'\b(?:bypass\s+(?:installed|in\s+place)|put\s+(?:the\s+)?bypass\s+in|crew\s+had\s+the\s+bypass\s+installed)\b', re.I)
            stop_pattern = re.compile(r'\b(?:took|pulled|removed)\b.{0,35}\bbypass\b|\bbypass\b.{0,35}\b(?:removed|out|pulled)\b', re.I)
            for note in notes:
                message = note.get('text', '')
                event_type = 'out' if stop_pattern.search(message) else 'in' if start_pattern.search(message) else None
                if event_type and note.get('created_at', '')[:10] <= plan.end.isoformat():
                    bypass_events.setdefault(note['asset_id'], []).append((note['created_at'], event_type, note))

            rows = []
            memo_paths = set()
            for asset_id, events in bypass_events.items():
                events.sort(key=lambda item: (item[0], item[2]['note_id']))
                active_start = None
                episodes = []
                for timestamp, event_type, note in events:
                    if event_type == 'in':
                        if active_start is None:
                            active_start = (timestamp, note)
                    elif active_start is not None:
                        episodes.append((active_start, (timestamp, note)))
                        active_start = None
                if active_start is not None:
                    episodes.append((active_start, None))

                for (started_at, start_note), stopped in episodes:
                    started_on = date.fromisoformat(started_at[:10])
                    stopped_at, stop_note = stopped if stopped else (None, None)
                    stopped_on = date.fromisoformat(stopped_at[:10]) if stopped_at else None
                    approvals = [memo for memo in memos if asset_id in memo['wells'] and memo['effective'] <= started_on]
                    approval = max(approvals, key=lambda memo: (memo['effective'], memo['memo_id']), default=None)
                    approval_id = (approval.get('current_memo_id') or approval['memo_id']) if approval else None
                    valid_until = approval['expires'] if approval else None
                    if not approval:
                        status = 'Not approved'
                    elif (stopped_on and stopped_on > valid_until) or (not stopped_on and plan.end > valid_until):
                        status = 'Expired'
                    else:
                        status = 'Allowed'

                    currently_in = stopped is None
                    if not currently_in and status == 'Allowed':
                        continue
                    area_id = store.assets[asset_id]['area_id']
                    ids = [start_note['note_id']] + ([stop_note['note_id']] if stop_note else [])
                    evidence.extend(ids)
                    if approval:
                        memo_paths.add(approval['path'])
                        if approval.get('extension_path'):
                            memo_paths.add(approval['extension_path'])
                    rows.append({
                        'asset_id': asset_id, 'area_id': area_id,
                        'bypass_in': started_on.isoformat(),
                        'bypass_out': stopped_on.isoformat() if stopped_on else 'still in',
                        'approval_id': approval_id or 'none',
                        'approval_valid_until': valid_until.isoformat() if valid_until else None,
                        'status': status, 'evidence_ids': ids,
                    })
            rows.sort(key=lambda row: (row['asset_id'], row['bypass_in']))
            extra_rule_paths.extend(sorted(memo_paths))
            metrics = {
                'bypasses_in_place': sum(row['bypass_out'] == 'still in' for row in rows),
                'allowed_in_place': sum(row['bypass_out'] == 'still in' and row['status'] == 'Allowed' for row in rows),
                'expired_or_unapproved': sum(row['status'] != 'Allowed' for row in rows),
            }
            explanation = "OPS-2 status is based on operator notes that put a flowline bypass in and take it out, checked against MOC well lists, effective dates, expiry dates and extensions. The table includes bypasses still in place plus past expired or unapproved bypasses."
        elif plan.operation == 'predictive_maintenance':
            inspections = query(f"SELECT * FROM ({base}) ORDER BY asset_id,inspected_at,inspection_id")
            inspections_by_asset = {}
            for inspection in inspections:
                inspections_by_asset.setdefault(inspection['asset_id'], []).append(inspection)
            selected_assets = [asset_id for asset_id, asset in store.assets.items() if (not plan.asset_ids or asset_id in plan.asset_ids) and (not selected_area or asset['area_id'] == selected_area)]
            window_start = effective_start.isoformat()
            repairs = [r for r in store.records('maintenance') if r['asset_id'] in selected_assets and window_start <= r['started_at'][:10] <= plan.end.isoformat() and r['maintenance_type'] == 'corrective']
            repairs_by_asset = {}
            for repair in repairs:
                repairs_by_asset.setdefault(repair['asset_id'], []).append(repair)
            spill_cutoff = plan.end - timedelta(days=90)
            rows = []
            for asset_id in selected_assets:
                observed = inspections_by_asset.get(asset_id, [])
                latest = observed[-1] if observed else None
                consecutive_stops = 0
                for inspection in reversed(observed):
                    if inspection['producing']:
                        break
                    consecutive_stops += 1
                recent_spills = [r for r in observed if r['spill_detected'] and r['inspected_at'].date() >= spill_cutoff]
                corrective = repairs_by_asset.get(asset_id, [])
                by_equipment = {}
                for repair in corrective:
                    by_equipment.setdefault(repair['equipment_kind'], []).append(repair)
                recurring_equipment, recurring_repairs = max(by_equipment.items(), key=lambda item: (len(item[1]), item[0]), default=(None, []))
                recurrence = len(recurring_repairs)
                recurrence_points = 40 if recurrence >= 3 else 20 if recurrence == 2 else 8 if recurrence == 1 else 0
                stopped_points = 30 if latest and not latest['producing'] else 0
                consecutive_points = min(max(consecutive_stops - 1, 0) * 10, 20)
                spill_points = 10 if recent_spills else 0
                score = min(recurrence_points + stopped_points + consecutive_points + spill_points, 100)
                priority = 'high' if score >= 60 else 'medium' if score >= 30 else 'low'
                signals = []
                if recurrence:
                    signals.append(f"{recurrence} corrective {recurring_equipment} repairs in 12 months (+{recurrence_points})")
                if latest and not latest['producing']:
                    signals.append(f"latest inspection is non-producing (+{stopped_points})")
                if consecutive_stops > 1:
                    signals.append(f"{consecutive_stops} consecutive non-producing inspections (+{consecutive_points})")
                if recent_spills:
                    signals.append(f"{len(recent_spills)} spill observation(s) in the last 90 days (+{spill_points})")
                if recurrence >= 3:
                    action = f"Start root-cause analysis for {recurring_equipment}; the manual threshold is met."
                elif latest and not latest['producing']:
                    action = f"Inspect {recurring_equipment or 'the well equipment'} and verify production state."
                elif recent_spills:
                    action = "Inspect the flowline and review spill-response follow-up."
                elif recurrence:
                    action = f"Schedule a condition inspection for {recurring_equipment}."
                else:
                    action = "Continue routine inspection; no elevated signal was found."
                row_evidence = [r['maintenance_id'] for r in recurring_repairs]
                if latest and not latest['producing']:
                    row_evidence.append(latest['inspection_id'])
                row_evidence.extend(r['inspection_id'] for r in recent_spills)
                evidence.extend(row_evidence)
                rows.append({'asset_id': asset_id, 'area_id': store.assets[asset_id]['area_id'], 'priority_score': score, 'priority': priority, 'recurring_equipment': recurring_equipment, 'corrective_repairs_12m': recurrence, 'consecutive_nonproducing': consecutive_stops, 'recent_spills_90d': len(recent_spills), 'latest_inspection': latest['inspected_at'] if latest else None, 'currently_producing': latest['producing'] if latest else None, 'signals': '; '.join(signals) or 'No elevated signal', 'recommended_action': action, 'evidence_ids': list(dict.fromkeys(row_evidence))})
            rows.sort(key=lambda row: (-row['priority_score'], row['asset_id']))
            metrics = {'analyzed_wells': len(rows), 'high_priority_wells': sum(r['priority'] == 'high' for r in rows), 'rca_required': sum(r['corrective_repairs_12m'] >= 3 for r in rows)}
            explanation = "Maintenance priority combines 12-month corrective recurrence, the latest production observation, consecutive non-producing inspections, and spill observations from the last 90 days. The score ranks attention; it is not a failure probability or remaining-useful-life estimate."
            warnings.append('Priority scores are deterministic triage signals from observed history, not a calibrated predictive model.')
        elif plan.operation in {'costs', 'maintenance', 'repair_reasons'}:
            repairs = store.records('maintenance')
            selected = [r for r in repairs if (not plan.asset_ids or r['asset_id'] in plan.asset_ids) and (not selected_area or store.assets[r['asset_id']]['area_id'] == selected_area) and (not plan.team_id or (store.assets[r['asset_id']].get('team_id') == plan.team_id or any(period.get('team_id') == plan.team_id and period['from'] <= r['started_at'][:10] and (not period.get('to') or r['started_at'][:10] <= period['to']) for period in store.assets[r['asset_id']].get('team_history', [])))) and r['started_at'][:10] <= plan.end.isoformat() and (not plan.start or r['started_at'][:10] >= plan.start.isoformat())]
            grouped = {}
            if plan.operation == 'repair_reasons':
                for repair in selected:
                    key = (repair['equipment_kind'], repair['issue'])
                    item = grouped.setdefault(key, {'equipment_kind': repair['equipment_kind'], 'issue': repair['issue'], 'action_taken': repair['action_taken'], 'repair_count': 0, 'corrective_count': 0, 'downtime_hours': 0, 'cost_usd': 0, 'affected_wells': [], 'evidence_ids': []})
                    item['repair_count'] += 1
                    item['corrective_count'] += int(repair['maintenance_type'] == 'corrective')
                    item['downtime_hours'] += repair['downtime_hours']
                    item['cost_usd'] += repair['cost_usd']['total']
                    item['affected_wells'].append(repair['asset_id'])
                    item['evidence_ids'].append(repair['maintenance_id'])
                rows = sorted(grouped.values(), key=lambda r: (-r['corrective_count'], -r['repair_count'], r['equipment_kind'], r['issue']))
                metrics = {'repair_count': len(selected), 'corrective_repairs': sum(r['corrective_count'] for r in rows), 'cost_usd': sum(r['cost_usd'] for r in rows), 'downtime_hours': sum(r['downtime_hours'] for r in rows)}
                explanation = "Repairs are grouped by the recorded equipment issue. Each row shows the action taken, affected wells, corrective count, downtime, cost, and source maintenance reports; these are recorded reasons, not inferred root causes."
                for item in rows:
                    item['affected_wells'] = sorted(set(item['affected_wells']))
                    evidence += item['evidence_ids']
            else:
                for r in selected:
                    key = (r['asset_id'], r['equipment_kind']) if plan.operation == 'maintenance' else (r['asset_id'],)
                    item = grouped.setdefault(key, {'asset_id': r['asset_id'], 'cost_usd': 0, 'repair_count': 0, 'corrective_count': 0, 'evidence_ids': []})
                    item['cost_usd'] += r['cost_usd']['total']
                    item['repair_count'] += 1
                    item['corrective_count'] += int(r['maintenance_type'] == 'corrective')
                    item['evidence_ids'].append(r['maintenance_id'])
                    if plan.operation == 'maintenance':
                        item['equipment_kind'] = r['equipment_kind']
                rows = sorted(grouped.values(), key=lambda r: (-r['corrective_count'] if plan.operation == 'maintenance' else -r['cost_usd'], r['asset_id']))
                metrics = {"cost_usd": sum(r['cost_usd'] for r in rows), "repair_count": len(selected)}
                explanation = "Repairs are filtered by start date. Costs are summed once per repair. Corrective recurrence is a maintenance-priority indicator, not a failure probability."
                if plan.operation == 'maintenance':
                    warnings.append('RCA thresholds and team policy overrides are not evaluated by this recurrence view yet.')
                for r in rows:
                    evidence += r['evidence_ids']
        else:
            rows = store.search(plan.search, plan.limit)
            explanation = "Relevant document excerpts. Search results alone do not establish a compliance conclusion."

        for row in rows:
            if 'area_id' in row:
                row['area'] = area_names[row['area_id']]
            if 'inspection_id' in row:
                rid = row['inspection_id']
                evidence.append(rid)
                row['corrections'] = store.changes.get(rid, [])
                evidence += row['corrections']
                if row['corrections']:
                    warnings.append(f"{rid}: corrected data applied. Original notes remain visible and may disagree.")
            for key, value in list(row.items()):
                if hasattr(value, 'isoformat'):
                    row[key] = value.isoformat()
        # Explicit full-result total and truncation prevent chart subsets masquerading as totals.
        total = len(rows)
        if total > plan.limit:
            warnings.append(f"Showing {plan.limit} of {total} result rows. Totals cover all matching rows.")
        rules = [{'path': r['path'], 'threshold_c': r['threshold']} for r in store.rules.values()]
        rules.extend({'path': path, 'threshold_c': 0} for path in extra_rule_paths if path not in {rule['path'] for rule in rules})
        visible = rows[:plan.limit]
        default_views = {'status': 'card', 'status_disagreements': 'table', 'temperatures': 'line', 'regional_temperatures': 'bar', 'hottest_wells': 'bar', 'downtime': 'summary' if len(rows) == 1 else 'bar', 'costs': 'bar', 'maintenance': 'bar', 'predictive_maintenance': 'bar'}
        resolved_view = default_views.get(plan.operation, 'table') if plan.view == 'auto' else plan.view
        return {'operation': plan.operation, 'plan': plan.model_dump(mode='json'), 'version': store.version, 'dataset_version': store.data_hash, 'as_of': plan.end.isoformat(), 'rows': visible, 'total_rows': total, 'metrics': metrics, 'explanation': explanation, 'warnings': list(dict.fromkeys(warnings)), 'evidence_ids': list(dict.fromkeys(evidence)), 'rules': rules, 'view': resolved_view, 'asset_ids': list(dict.fromkeys(r['asset_id'] for r in visible if 'asset_id' in r))}
