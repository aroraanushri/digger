"""Validate a replacement dataset by running the real ingestion and projection pipeline."""
import argparse
import json
import shutil
import tempfile
from pathlib import Path

from .store import Store


REQUIRED_ASSET_FILES = ("metadata.json", "inspections.jsonl", "maintenance.jsonl", "context.jsonl", "regulatory-events.jsonl")


def validate(source: Path, existing_state: Path | None = None) -> dict:
    source = source.resolve()
    issues: list[str] = []
    with tempfile.TemporaryDirectory(prefix="digger-dataset-check-") as temp_name:
        validation_state = Path(temp_name)
        ledger = existing_state.resolve() / "knowledge.sqlite" if existing_state else None
        if ledger and ledger.is_file():
            shutil.copy2(ledger, validation_state / "knowledge.sqlite")
        store = Store.__new__(Store)
        try:
            Store.__init__(store, source, validation_state)
            assets = store.assets
            inspections = store.records("inspections")
            maintenance = store.records("maintenance")
            regulatory = store.records("regulatory-events")
            context = store.records("context")
            inspection_ids = {row["inspection_id"] for row in inspections}
            user_ids = set(store.users)

            areas_path = source / "fleet" / "areas.json"
            if not areas_path.is_file():
                issues.append("Missing fleet/areas.json")
                areas = []
            else:
                areas_document = json.loads(areas_path.read_text(encoding="utf-8"))
                areas = areas_document.get("areas", areas_document) if isinstance(areas_document, dict) else areas_document
            area_ids = {area["area_id"] for area in areas}
            declared_area_assets = {asset_id for area in areas for asset_id in area.get("asset_ids", [])}

            for asset_id, asset in assets.items():
                asset_dir = source / "assets" / asset_id
                for filename in REQUIRED_ASSET_FILES:
                    if not (asset_dir / filename).is_file():
                        issues.append(f"{asset_id}: missing {filename}")
                if asset.get("area_id") not in area_ids:
                    issues.append(f"{asset_id}: unknown area {asset.get('area_id')}")
                if asset_id not in declared_area_assets:
                    issues.append(f"{asset_id}: absent from fleet/areas.json asset_ids")
                if asset.get("well_type") not in store.rules:
                    issues.append(f"{asset_id}: no parsed manual for well type {asset.get('well_type')}")

            for kind, rows in (("inspection", inspections), ("maintenance", maintenance), ("regulatory event", regulatory), ("context note", context)):
                for row in rows:
                    if row.get("asset_id") not in assets:
                        issues.append(f"{kind} references unknown asset {row.get('asset_id')}")
            for row in inspections:
                if row.get("inspector_user_id") not in user_ids:
                    issues.append(f"{row['inspection_id']}: unknown inspector {row.get('inspector_user_id')}")
            for row in maintenance:
                trigger = row.get("triggered_by_inspection_id")
                if trigger and trigger not in inspection_ids:
                    issues.append(f"{row['maintenance_id']}: unknown triggering inspection {trigger}")
                report_file = row.get("report_file")
                if report_file and not (source / "assets" / row["asset_id"] / report_file).is_file():
                    issues.append(f"{row['maintenance_id']}: missing report {report_file}")
            for row in regulatory:
                related = row.get("related_inspection_id")
                if related and related not in inspection_ids:
                    issues.append(f"{row['event_id']}: unknown related inspection {related}")
            for row in context:
                related = row.get("inspection_id")
                if related and related not in inspection_ids:
                    issues.append(f"{row['note_id']}: unknown related inspection {related}")

            for event in store.sql.execute("SELECT version,payload FROM events ORDER BY version"):
                payload = json.loads(event["payload"])
                label = f"knowledge update v{event['version']}"
                if payload.get("user_id") not in user_ids:
                    issues.append(f"{label}: author {payload.get('user_id')} is absent from fleet/users.json")
                if payload.get("kind") != "policy" and payload.get("asset_id") not in assets:
                    issues.append(f"{label}: asset {payload.get('asset_id')} is absent from the replacement dataset")
                if payload.get("kind") == "correction" and payload.get("target_id") not in store.effective:
                    issues.append(f"{label}: target {payload.get('target_id')} is absent from the replacement dataset")
                if payload.get("kind") == "policy" and payload.get("scope_well_type") not in store.rules:
                    issues.append(f"{label}: well type {payload.get('scope_well_type')} has no replacement manual")

            if issues:
                raise ValueError("Dataset validation failed:\n- " + "\n- ".join(issues[:100]))
            return {
                "dataset": str(source),
                "dataset_version": store.data_hash,
                "wells": len(assets),
                "inspections": len(inspections),
                "maintenance_records": len(maintenance),
                "regulatory_events": len(regulatory),
                "context_notes": len(context),
                "documents": sum(1 for _ in source.rglob("*.md")),
                "areas": len(areas),
                "well_types": sorted(store.rules),
                "replayed_knowledge_version": store.version,
            }
        finally:
            if hasattr(store, "db"):
                store.db.close()
            if hasattr(store, "sql"):
                store.sql.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, help="Path containing the assets/ and fleet/ directories")
    parser.add_argument("--state", type=Path, help="Existing Digger state directory whose knowledge ledger should be replayed")
    args = parser.parse_args()
    try:
        print(json.dumps(validate(args.dataset, args.state), indent=2))
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
