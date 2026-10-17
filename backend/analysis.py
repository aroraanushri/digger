import json
from datetime import timedelta

from .models import QueryPlan
from .store import Store


def analyze(store: Store, plan: QueryPlan):
    with store.lock:
        if getattr(store, "projection_failed", False):
            raise RuntimeError("An update is saved but its analytical projection needs a restart.")
        unknown = set(plan.asset_ids) - set(store.assets)
        if unknown:
            raise ValueError(f"Unknown assets: {', '.join(sorted(unknown))}")
        areas = json.loads((store.source / "fleet/areas.json").read_text())['areas']
        area_names = {a['area_id']: a['name'] for a in areas}
        selected_area = None
        if plan.area:
            matches = [k for k, v in area_names.items() if plan.area.casefold() in (k.casefold(), v.casefold())]
            if not matches:
                raise ValueError("Unknown area; use a supplied area ID or full name")
            selected_area = matches[0]
        filters, args = ["CAST(inspected_at AS DATE) <= ?"], [plan.end]
        if plan.start:
            filters.append("CAST(inspected_at AS DATE) >= ?")
            args.append(plan.start)
        if plan.asset_ids:
            filters.append("asset_id IN (SELECT unnest(?))")
            args.append(plan.asset_ids)
        if selected_area:
            filters.append("area_id=?")
            args.append(selected_area)
        base = "SELECT * FROM effective WHERE " + " AND ".join(filters)
        evidence = []
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
            for row in rows:
                asset = store.assets[row['asset_id']]
                row['recorded_status'] = asset['recorded_status']
                row['context'] = [n for n in notes if n['asset_id'] == row['asset_id'] and n['valid_from'] <= plan.end.isoformat() and (not n.get('valid_to') or n['valid_to'] >= plan.end.isoformat())]
                updates = [json.loads(e['payload']) for e in store.sql.execute('SELECT version,payload FROM events') if json.loads(e['payload'])['asset_id'] == row['asset_id']]
                row['updates'] = updates
                row['context'].extend({'note_id': f"UPDATE-{event['version']}", 'note_type': event['kind'], 'text': event['reason'], 'author_user_id': event['user_id'], 'valid_from': event.get('inspection', {}).get('inspected_at', plan.end.isoformat())[:10], 'valid_to': None} for event in updates if event['kind'] == 'note')
                for n in row['context']:
                    evidence.append(n['note_id'])
                if row['recorded_status'] == 'active' and not row['producing']:
                    warnings.append(f"{row['asset_id']}: recorded active status disagrees with latest inspection.")
        elif plan.operation == "temperatures":
            rows = query(f"SELECT * FROM ({base}) ORDER BY inspected_at,asset_id")
            explanation = "Observed temperatures and their difference. Production uses a type-specific threshold; missing inspections are not interpolated."
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
        elif plan.operation in {'costs', 'maintenance', 'repair_reasons'}:
            repairs = store.records('maintenance')
            selected = [r for r in repairs if (not plan.asset_ids or r['asset_id'] in plan.asset_ids) and (not selected_area or store.assets[r['asset_id']]['area_id'] == selected_area) and r['started_at'][:10] <= plan.end.isoformat() and (not plan.start or r['started_at'][:10] >= plan.start.isoformat())]
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
        visible = rows[:plan.limit]
        return {'operation': plan.operation, 'plan': plan.model_dump(mode='json'), 'version': store.version, 'dataset_version': store.data_hash, 'as_of': plan.end.isoformat(), 'rows': visible, 'total_rows': total, 'metrics': metrics, 'explanation': explanation, 'warnings': list(dict.fromkeys(warnings)), 'evidence_ids': list(dict.fromkeys(evidence)), 'rules': rules, 'view': plan.view, 'asset_ids': list(dict.fromkeys(r['asset_id'] for r in visible if 'asset_id' in r))}
