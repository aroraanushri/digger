from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.agent import field_update_from_chat, ollama_query_schema, semantic_plan, update_answer
from backend.app import create_app
from backend.models import ChatRequest, QueryPlan
from backend.validate_dataset import validate

SOURCE = Path(__file__).resolve().parents[1] / 'flytbase-ahc-swe-asset-intelligence-dataset-main/sample-data'
TEST_SOURCE = Path(__file__).resolve().parents[1] / 'flytbase-ahc-swe-asset-intelligence-dataset-main/test-data/dataset'


@pytest.fixture(scope='module')
def client(tmp_path_factory):
    with TestClient(create_app(SOURCE, tmp_path_factory.mktemp('digger'))) as client:
        yield client


def query(client, operation, **kwargs):
    payload = {'operation': operation, **kwargs}
    payload.setdefault('end', '2026-10-16')
    response = client.post('/api/analyze', json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_ollama_schema_avoids_unsupported_large_grammar_bounds():
    schema = ollama_query_schema()
    assert 'maxItems' not in schema['properties']['asset_ids']
    assert 'maxLength' not in schema['properties']['search']
    with pytest.raises(ValueError):
        QueryPlan(operation='status', asset_ids=['WELL-0001'] * 2001)


def test_dataset_counts_and_idempotent_ingestion(client):
    health = client.get('/api/health').json()
    assert health['assets'] == 200
    assert health['inspections'] == 39567
    client.app.state.store.ingest()
    assert client.get('/api/health').json() == health


def test_replacement_dataset_validation_pipeline():
    report = validate(SOURCE)
    assert report['wells'] == 200
    assert report['inspections'] == 39567
    assert report['areas'] == 4
    assert report['well_types'] == ['T200', 'T350']


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
    assert result['view'] == 'line'


def test_regional_temperatures_rank_all_areas(client):
    result = query(client, 'regional_temperatures', end='2026-10-16', view='bar')
    assert result['view'] == 'bar'
    assert result['metrics']['regions_compared'] == 4
    assert result['rows'][0]['avg_delta_c'] >= result['rows'][-1]['avg_delta_c']
    assert {'area', 'avg_delta_c', 'delta_stddev_c', 'observations'} <= result['rows'][0].keys()
    user = client.get('/api/users').json()[0]['user_id']
    response = client.post('/api/chat', json={'user_id': user, 'thread_id': 'hotter-region', 'message': 'Which region of the field consistently runs hotter?', 'as_of': '2026-10-16'})
    assert response.status_code == 200, response.text
    assert response.json()['operation'] == 'regional_temperatures'


def test_hottest_wells_rank_observed_temperature_difference(client):
    result = query(client, 'hottest_wells', end='2026-10-16', limit=10, view='bar')
    assert result['view'] == 'bar'
    assert result['total_rows'] == 200
    assert len(result['rows']) == 10
    assert result['rows'][0]['avg_delta_c'] >= result['rows'][-1]['avg_delta_c']
    assert {'asset_id', 'area', 'avg_delta_c', 'observations'} <= result['rows'][0].keys()
    user = client.get('/api/users').json()[0]['user_id']
    response = client.post('/api/chat', json={'user_id': user, 'thread_id': 'hottest-wells', 'message': 'Which wells are hottest?', 'as_of': '2026-10-16'})
    assert response.status_code == 200, response.text
    assert response.json()['operation'] == 'hottest_wells'


def test_status_disagreements_only_returns_conflicting_operator_notes(client):
    result = query(client, 'status_disagreements', end='2027-01-15', view='table')
    assert result['view'] == 'table'
    assert result['total_rows'] > 0
    assert result['total_rows'] < 600
    assert {'latest_inspection_id', 'latest_operator_note', 'status_now', 'status_basis'} <= result['rows'][0].keys()
    user = client.get('/api/users').json()[0]['user_id']
    response = client.post('/api/chat', json={'user_id': user, 'thread_id': 'status-conflicts', 'message': 'Across the whole field, for which wells do the latest inspection and the operator notes disagree about whether the well is producing oil?', 'as_of': '2027-01-15'})
    assert response.status_code == 200, response.text
    assert response.json()['operation'] == 'status_disagreements'


def test_predictive_maintenance_priorities_are_explainable(client):
    result = query(client, 'predictive_maintenance', limit=10)
    assert result['view'] == 'bar'
    assert result['metrics']['analyzed_wells'] == 200
    assert result['rows'] == sorted(result['rows'], key=lambda row: (-row['priority_score'], row['asset_id']))
    assert {'priority_score', 'priority', 'signals', 'recommended_action', 'evidence_ids'} <= result['rows'][0].keys()
    assert 0 <= result['rows'][0]['priority_score'] <= 100
    assert 'failure probability' in result['explanation']


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


def test_repair_reasons_explain_maintenance_issues(client):
    result = query(client, 'repair_reasons', area='Northeast Block 7', limit=20)
    assert result['total_rows'] > 0
    assert result['metrics']['repair_count'] > 0
    assert {'equipment_kind', 'issue', 'action_taken', 'affected_wells'} <= result['rows'][0].keys()
    user = client.get('/api/users').json()[0]['user_id']
    response = client.post('/api/chat', json={'user_id': user, 'thread_id': 'repair-why', 'message': 'why were repairs needed in Northeast Block 7', 'as_of': '2026-10-16'})
    assert response.status_code == 200, response.text
    assert response.json()['operation'] == 'repair_reasons'


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

def test_chat_prepares_reviewable_field_update(client):
    user = client.get('/api/users').json()[0]['user_id']
    request = ChatRequest(user_id=user, thread_id='chat-update', message='correct WELL-0164 inspection INS-0164-0205 wellhead temperature to 41 because the crew remeasured it')
    proposal = field_update_from_chat(client.app.state.store, request)
    assert proposal['kind'] == 'correction'
    assert proposal['field'] == 'wellhead_temp_c'
    assert proposal['value'] == 41.0
    assert proposal['expected_version'] == client.app.state.store.version

    spill_request = request.model_copy(update={'message': 'correct WELL-0164 inspection INS-0164-0205 spill detection to false because it was rainwater'})
    spill_proposal = field_update_from_chat(client.app.state.store, spill_request)
    assert spill_proposal['value'] is False
    stream = client.post('/agent', json={'threadId': f'{user}:2026-10-16:0', 'runId': 'chat-update-run', 'messages': [{'role': 'user', 'content': request.message}]})
    assert stream.status_code == 200
    assert 'field_update' in stream.text and 'INS-0164-0205' in stream.text


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


def test_document_linked_semantic_analyses(tmp_path):
    with TestClient(create_app(SOURCE, tmp_path / 'semantic')) as local:
        status = query(local, 'status', asset_ids=['WELL-0001'])
        assert status['rows'][0]['inspector_name'] == 'Ryan Brooks'
        assert status['rows'][0]['method'] == 'drone'
        assert 'CTX-0001-003' in status['evidence_ids']

        compliance = query(local, 'compliance', asset_ids=['WELL-0001'], end='2026-10-16')
        assert {row['protocol'] for row in compliance['rows'] if row['status'] == 'breach'} == {'SR-01', 'SR-02', 'SR-03', 'RC-04'}
        assert compliance['metrics'] == {'breaches': 4, 'open_fines_usd': 5000}
        assert {'Manual §10', 'Manual records'} <= {row['protocol'] for row in compliance['rows'] if row['status'] == 'pending'}
        assert any(row['protocol'] == 'RC-02' and row['status'] == 'met' for row in compliance['rows'])

        containment = query(local, 'containment', area='Southwest Block 24', start='2026-07-18', end='2026-10-16')
        assert {row['asset_id']: row['containment_hours'] for row in containment['rows']} == {'WELL-0082': 7.1, 'WELL-0100': 8.1}

        rca = query(local, 'rca', area='Northeast Block 7', equipment_kind='pump_jack', end='2026-10-16')
        assert {row['asset_id'] for row in rca['rows']} == {'WELL-0108', 'WELL-0121', 'WELL-0138'}

        review = query(local, 'spill_review', asset_ids=['WELL-0101'], start='2026-08-20', end='2026-08-20')
        row = review['rows'][0]
        assert row['inspection_id'] == 'INS-0101-0197'
        assert row['linked_repairs'] == ['MNT-0101-029']
        assert any('Ryan Brooks' in note and 'produced water' in note for note in row['disagreement'])
        assert any('Chris Dalton' in note and 'crude' in note for note in row['disagreement'])


def test_natural_corrections_scoped_policy_and_planned_work(tmp_path):
    with TestClient(create_app(SOURCE, tmp_path / 'knowledge-semantics')) as local:
        users = {user['name']: user['user_id'] for user in local.get('/api/users').json()}
        store = local.app.state.store

        temperature_request = ChatRequest(
            user_id=users['Michael Turner'], thread_id='temperature-correction', as_of='2026-10-16',
            message='The 15 Oct reading for WELL-0164 is wrong. The handheld thermal gun used in that inspection was faulty; the field crew re-measured the wellhead at 41.0 °C.',
        )
        temperature = field_update_from_chat(store, temperature_request)
        assert (temperature['target_id'], temperature['field'], temperature['value']) == ('INS-0164-0205', 'wellhead_temp_c', 41.0)
        assert local.post('/api/knowledge', json=temperature).status_code == 200
        corrected = query(local, 'status', asset_ids=['WELL-0164'], end='2026-10-16')
        assert corrected['rows'][0]['producing'] is True
        assert corrected['rows'][0]['corrections_applied'][0]['author'] == 'Michael Turner'
        assert any('not moving' in warning for warning in corrected['warnings'])

        spill_request = ChatRequest(
            user_id=users['Michael Turner'], thread_id='spill-correction', as_of='2026-10-16',
            message='The AI estimated the 24 Sep spill at WELL-0151 as 4.5 bbl. The field crew measured 5.4 bbl.',
        )
        spill = field_update_from_chat(store, spill_request)
        assert (spill['target_id'], spill['field'], spill['value']) == ('INS-0151-0201', 'spill_est_volume_bbl', 5.4)
        preview = update_answer(store, spill)
        assert 'SR-01' in preview and 'SR-02' in preview and 'SR-03' in preview
        assert local.post('/api/knowledge', json=spill).status_code == 200
        reportable = query(local, 'reportable_spills', area='Northeast Block 8', start='2026-07-18', end='2026-10-16')
        assert [(row['asset_id'], row['spill_est_volume_bbl'], row['report_status']) for row in reportable['rows']] == [('WELL-0151', 5.4, 'overdue')]

        policy_request = ChatRequest(
            user_id=users['Michael Turner'], thread_id='policy', as_of='2026-10-16',
            message='Note for all T350 wells: our team now wants a pump jack root-cause analysis after 2 repairs, not 3.',
        )
        policy = field_update_from_chat(store, policy_request)
        assert policy['kind'] == 'policy' and policy['scope_well_type'] == 'T350' and policy['corrective_threshold'] == 2
        assert local.post('/api/knowledge', json=policy).status_code == 200
        rca = query(local, 'rca', area='Northeast Block 7', equipment_kind='pump_jack', end='2026-10-16')
        assert {row['asset_id'] for row in rca['rows']} == {'WELL-0108', 'WELL-0120', 'WELL-0121', 'WELL-0138'}
        assert 'WELL-0102' not in {row['asset_id'] for row in rca['rows']}
        assert next(row for row in rca['rows'] if row['asset_id'] == 'WELL-0120')['rule_source'] == 'User policy by Michael Turner'

        plan_request = ChatRequest(
            user_id=users['Jake Morales'], thread_id='planned', as_of='2026-10-16',
            message='FYI for everyone: WELL-0007 will be shut in from 17 Oct for a 10-day planned workover.',
        )
        planned = field_update_from_chat(store, plan_request)
        assert planned['valid_from'] == '2026-10-17' and planned['valid_to'] == '2026-10-27'
        assert planned['confirmation_status'] == 'confirmed'
        assert local.post('/api/knowledge', json=planned).status_code == 200
        downtime = query(local, 'planned_downtime', asset_ids=['WELL-0007'], end='2026-10-16')
        assert downtime['rows'][0]['author'] == 'Jake Morales'

        supported = field_update_from_chat(store, ChatRequest(user_id=users['Michael Turner'], thread_id='conflict', as_of='2026-10-16', message='WELL-0175 has been shut in for planned work since 14 Oct.'))
        disputed = field_update_from_chat(store, ChatRequest(user_id=users['Tyler Hughes'], thread_id='conflict', as_of='2026-10-16', message='WELL-0175 is running fine.'))
        contradicted = field_update_from_chat(store, ChatRequest(user_id=users['Jake Morales'], thread_id='conflict', as_of='2026-10-16', message='WELL-0001 is back online since 14 Oct.'))
        assert supported['confirmation_status'] == 'supported'
        assert disputed['confirmation_status'] == 'unconfirmed'
        assert contradicted['confirmation_status'] == 'unconfirmed'


def test_semantic_planner_keeps_conversation_memory_private(tmp_path):
    with TestClient(create_app(SOURCE, tmp_path / 'memory-semantics')) as local:
        users = {user['name']: user['user_id'] for user in local.get('/api/users').json()}
        store = local.app.state.store
        store.remember(users['Michael Turner'], 'q6', 'Which three wells?', 'answer', ['WELL-0121', 'WELL-0138', 'WELL-0107'])
        michael = ChatRequest(user_id=users['Michael Turner'], thread_id='new', as_of='2026-10-16', message='For the three wells we discussed yesterday, what have repairs cost this year?')
        assert semantic_plan(michael, store.history(users['Michael Turner'])).asset_ids == ['WELL-0121', 'WELL-0138', 'WELL-0107']
        jessica = ChatRequest(user_id=users['Jessica Reed'], thread_id='new', as_of='2026-10-16', message='For the three wells we discussed yesterday, what have repairs cost this year?')
        with pytest.raises(ValueError, match='No earlier well selection exists'):
            semantic_plan(jessica, store.history(users['Jessica Reed']))

        missing = local.post('/api/chat', json={'user_id': users['Jessica Reed'], 'thread_id': 'new-chat', 'as_of': '2026-10-16', 'message': 'For the three wells we discussed yesterday, what have repairs cost this year?'})
        assert missing.status_code == 200
        assert missing.json()['kind'] == 'message'
        assert 'do not have' in missing.json()['answer'].replace("don't", 'do not')

        stream = local.post('/agent', json={'threadId': f"{users['Jessica Reed']}:2026-10-16:new-chat", 'runId': 'missing-memory', 'messages': [{'role': 'user', 'content': 'For the three wells we discussed yesterday, what have repairs cost this year?'}]})
        assert stream.status_code == 200
        assert 'TEXT_MESSAGE_CONTENT' in stream.text
        assert 'RUN_FINISHED' in stream.text
        assert 'RUN_ERROR' not in stream.text


def test_pressure_question_returns_chat_limitation_without_empty_map(tmp_path):
    with TestClient(create_app(TEST_SOURCE, tmp_path / 'pressure-data')) as local:
        user = local.get('/api/users').json()[0]['user_id']
        message = 'All our wells feed one reservoir. Map the reservoir pressure across the field from the pressure data.'
        response = local.post('/api/chat', json={'user_id': user, 'thread_id': 'pressure', 'message': message, 'as_of': '2027-01-15'})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['kind'] == 'message'
        assert 'no numeric pressure measurements' in result['answer']
        assert 'units and measurement dates' in result['answer']
        assert 'rows' not in result

        stream = local.post('/agent', json={
            'threadId': f'{user}:2027-01-15:pressure', 'runId': 'pressure-run',
            'messages': [{'role': 'user', 'content': message}],
        })
        assert stream.status_code == 200
        assert 'no numeric pressure measurements' in stream.text
        assert 'show_analysis' not in stream.text
        assert 'RUN_ERROR' not in stream.text


def test_production_ranking_question_explains_missing_measurements(tmp_path):
    with TestClient(create_app(TEST_SOURCE, tmp_path / 'production-data')) as local:
        user = local.get('/api/users').json()[0]['user_id']
        response = local.post('/api/chat', json={
            'user_id': user, 'thread_id': 'production-ranking',
            'message': 'Which well produced the most oil in 2026?', 'as_of': '2027-01-15',
        })
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['kind'] == 'message'
        assert 'no per-well oil production volumes or rates' in result['answer']
        assert 'Spill volumes are not production volumes' in result['answer']
        assert 'rows' not in result


def test_gas_response_question_returns_ops1_deadline_table(tmp_path):
    with TestClient(create_app(TEST_SOURCE, tmp_path / 'gas-response')) as local:
        user = next(item['user_id'] for item in local.get('/api/users').json() if item['name'] == 'Laura Simmons')
        response = local.post('/api/chat', json={
            'user_id': user, 'thread_id': 'gas-response',
            'message': 'Since 1 Nov 2026, every time an operator note reports gas at a well, did we get someone out to that well in time under our operating procedures? List every report and whether the response met the deadline.',
            'as_of': '2027-01-15',
        })
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['operation'] == 'gas_response'
        assert result['view'] == 'table'
        assert result['metrics']['gas_reports'] == result['total_rows'] == 26
        assert {'asset_id', 'area', 'reported_at', 'response_id', 'response_at', 'hours_to_response', 'deadline_hours', 'result'} <= result['rows'][0].keys()
        assert {row['deadline_hours'] for row in result['rows']} == {12, 24}
        assert {row['result'] for row in result['rows']} <= {'Met', 'Missed'}
        assert 'fleet/sops/operations-sop-rev2.md' in {rule['path'] for rule in result['rules']}
        assert all(row['note_text'] and row['evidence_ids'][0] in result['evidence_ids'] for row in result['rows'])


def test_bypass_question_shows_current_and_historical_violations(tmp_path):
    with TestClient(create_app(TEST_SOURCE, tmp_path / 'bypass-review')) as local:
        user = local.get('/api/users').json()[0]['user_id']
        response = local.post('/api/chat', json={
            'user_id': user, 'thread_id': 'bypass-review',
            'message': 'Across all areas, which temporary flowline bypasses are in place right now, and is each one allowed? Also list any bypass, past or present, that stayed in after its approval ran out or was never approved at all.',
            'as_of': '2027-01-15',
        })
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['operation'] == 'bypasses' and result['view'] == 'table'
        assert result['metrics'] == {'bypasses_in_place': 3, 'allowed_in_place': 1, 'expired_or_unapproved': 5}
        assert result['total_rows'] == 6
        rows = {row['asset_id']: row for row in result['rows']}
        assert rows['WELL-0248']['status'] == 'Allowed'
        assert rows['WELL-0248']['approval_id'] == 'MOC-2026-044'
        assert rows['WELL-0248']['approval_valid_until'] == '2027-02-28'
        assert rows['WELL-0249']['status'] == 'Expired' and rows['WELL-0249']['bypass_out'] == 'still in'
        assert rows['WELL-0087']['status'] == 'Expired' and rows['WELL-0087']['bypass_out'] == '2027-01-06'
        assert rows['WELL-0158']['status'] == 'Expired' and rows['WELL-0158']['bypass_out'] == '2026-12-28'
        assert rows['WELL-0405']['status'] == 'Not approved' and rows['WELL-0405']['approval_id'] == 'none'
        assert rows['WELL-0561']['status'] == 'Not approved' and rows['WELL-0561']['bypass_out'] == '2026-12-19'
        assert 'fleet/sops/moc-2026-044.md' in {rule['path'] for rule in result['rules']}
        assert all(row['evidence_ids'][0] in result['evidence_ids'] for row in rows.values())


@pytest.mark.parametrize(('message', 'operation', 'view', 'start', 'limit'), [
    ('Is WELL-0001 producing oil right now? Show me its status card.', 'status', 'card', None, 200),
    ("Show a line chart of WELL-0001's wellhead temperature against air temperature for the last 13 weeks.", 'temperatures', 'line', '2026-07-17', 200),
    ('How many oil spills has WELL-0001 had in the last 4 years? List them in a table.', 'spills', 'table', '2022-10-16', 200),
    ('How many days was WELL-0001 down in 2025?', 'downtime', 'auto', '2025-01-01', 200),
    ('Which 3 wells in Northeast Block 7 had the most downtime in the last 4 months? Show a bar chart.', 'downtime', 'bar', '2026-06-16', 3),
    ('Show on a map every well in Northeast Block 8 with a real spill in the last 90 days.', 'spills', 'map', '2026-07-18', 200),
    ('Show a heatmap of 2026 spills by geographic area and month.', 'spills', 'heatmap', '2026-01-01', 200),
    ('Which wells in Northeast Block 8 are not producing oil right now, and why? Show them in a graph.', 'status', 'bar', None, 200),
])
def test_core_question_shapes_route_without_model(message, operation, view, start, limit):
    request = ChatRequest(user_id='U-101', thread_id='routing', as_of='2026-10-16', message=message)
    plan = semantic_plan(request, [])
    assert plan.operation == operation
    assert plan.view == view
    assert (plan.start.isoformat() if plan.start else None) == start
    assert plan.limit == limit
