from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app

SOURCE = Path(__file__).resolve().parents[1] / 'flytbase-ahc-swe-asset-intelligence-dataset-main/sample-data'


@pytest.fixture(scope='module')
def client(tmp_path_factory):
    with TestClient(create_app(SOURCE, tmp_path_factory.mktemp('digger'))) as client:
        yield client


def query(client, operation, **kwargs):
    response = client.post('/api/analyze', json={'operation': operation, **kwargs})
    assert response.status_code == 200, response.text
    return response.json()


def test_dataset_counts_and_idempotent_ingestion(client):
    health = client.get('/api/health').json()
    assert health['assets'] == 200
    assert health['inspections'] == 39567
    client.app.state.store.ingest()
    assert client.get('/api/health').json() == health


def test_reference_status(client):
    result = query(client, 'status', asset_ids=['WELL-0001'])
    row = result['rows'][0]
    assert row['inspection_id'] == 'INS-0001-0203'
    assert row['delta_c'] == pytest.approx(3.2)
    assert row['producing'] is False
    assert 'CTX-0001-003' in result['evidence_ids']


def test_reference_temperatures(client):
    result = query(client, 'temperatures', asset_ids=['WELL-0001'], start='2026-07-23')
    assert result['total_rows'] == 13
    assert sum(not r['producing'] for r in result['rows']) == 2


def test_corrected_spills_and_downtime(client):
    result = query(client, 'spills', asset_ids=['WELL-0001'], start='2022-10-16')
    assert result['metrics']['spill_count'] == 6
    assert result['metrics']['volume_bbl'] == pytest.approx(15.1)
    assert 'INS-0001-0070' not in [r['inspection_id'] for r in result['rows']]
    result = query(client, 'downtime', asset_ids=['WELL-0001'], start='2025-01-01', end='2025-12-31')
    assert result['metrics']['downtime_days'] == 21


def test_area_ranking_and_costs(client):
    result = query(client, 'downtime', area='Northeast Block 7', start='2026-06-16', limit=3)
    assert {r['asset_id']: r['downtime_days'] for r in result['rows']} == {'WELL-0121': 49, 'WELL-0107': 42, 'WELL-0138': 42}
    result = query(client, 'costs', asset_ids=['WELL-0121','WELL-0138','WELL-0107'], start='2026-01-01')
    assert {r['asset_id']: r['cost_usd'] for r in result['rows']} == {'WELL-0121': 25794, 'WELL-0138': 46942, 'WELL-0107': 5176}


def test_map_spills(client):
    result = query(client, 'spills', area='Northeast Block 8', start='2026-07-18', view='map')
    assert {r['asset_id'] for r in result['rows']} == {'WELL-0151','WELL-0165','WELL-0175','WELL-0186'}


def test_correction_is_shared_audited_idempotent_and_versioned(client):
    before = query(client, 'status', area='Northeast Block 8', only_nonproducing=True)
    assert before['total_rows'] == 4
    user = client.get('/api/users').json()[0]['user_id']
    payload = {'request_id': str(uuid4()), 'user_id': user, 'asset_id': 'WELL-0164', 'expected_version': before['version'], 'kind': 'correction', 'target_id': 'INS-0164-0205', 'field': 'wellhead_temp_c', 'value': 41.0, 'reason': 'Handheld thermal gun faulty; crew remeasured.'}
    response = client.post('/api/knowledge', json=payload)
    assert response.status_code == 200, response.text
    assert client.post('/api/knowledge', json=payload).json()['duplicate']
    after = query(client, 'status', area='Northeast Block 8', only_nonproducing=True)
    assert after['total_rows'] == 3
    original = client.get('/api/evidence/INS-0164-0205').json()
    assert original['record']['wellhead_temp_c'] == 24.0
    assert original['corrections']
    payload['request_id'] = str(uuid4())
    assert client.post('/api/knowledge', json=payload).status_code == 409


def test_new_inspection_and_date_boundary(client):
    user = client.get('/api/users').json()[0]['user_id']
    payload = {'request_id': str(uuid4()), 'user_id': user, 'asset_id': 'WELL-0001', 'expected_version': client.get('/api/health').json()['version'], 'kind': 'inspection', 'reason': 'Confirmed new field observation', 'inspection': {'inspected_at': '2026-10-17T09:00:00Z', 'wellhead_temp_c': 50, 'ambient_temp_c': 20}}
    response = client.post('/api/knowledge', json=payload)
    assert response.status_code == 200, response.text
    assert not query(client,'status',asset_ids=['WELL-0001'])['rows'][0]['producing']
    assert query(client,'status',asset_ids=['WELL-0001'],end='2026-10-17')['rows'][0]['producing']


def test_private_memory_and_document_safety(client):
    users = client.get('/api/users').json()
    store = client.app.state.store
    store.remember(users[0]['user_id'], 'a', 'my private question', 'answer', ['WELL-0001'])
    assert client.get('/api/history/' + users[0]['user_id']).json()[0]['question'] == 'my private question'
    assert client.get('/api/history/' + users[1]['user_id']).json() == []
    assert client.get('/api/document', params={'path':'../../README.md'}).status_code == 404
    assert client.post('/api/analyze', json={'operation':'sql','query':'DROP TABLE records'}).status_code == 422


def test_ag_ui_initial_run(client):
    response = client.post('/agent', json={'threadId':'U-101:test', 'runId':'test-run', 'messages':[]})
    assert response.status_code == 200
    assert 'RUN_STARTED' in response.text and 'RUN_FINISHED' in response.text
