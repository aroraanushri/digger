import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from .agent import ModelUnavailable, chat
from .analysis import analyze
from .models import ChatRequest, KnowledgeCommand, QueryPlan
from .store import Store

ROOT = Path(__file__).resolve().parents[1]


def create_app(source=None, state=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.store = Store(Path(source or os.environ.get('DIGGER_DATASET', ROOT / 'flytbase-ahc-swe-asset-intelligence-dataset-main/sample-data')), Path(state or os.environ.get('DIGGER_STATE', ROOT / 'data')))
        app.state.model_lock = asyncio.Lock()
        yield
        app.state.store.db.close()
        app.state.store.sql.close()

    app = FastAPI(title='Digger', version='0.1.0', lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=['http://localhost:3000', 'http://127.0.0.1:3000'], allow_methods=['GET', 'POST'], allow_headers=['Content-Type'])

    @app.get('/api/health')
    def health():
        s = app.state.store
        with s.lock:
            return {'status': 'ok', 'assets': len(s.assets), 'inspections': len(s.effective), 'version': s.version, 'dataset_version': s.data_hash, 'model': os.environ.get('OLLAMA_MODEL', 'gemma4:e2b-it-q4_K_M'), 'authentication': 'demo user picker; local trusted use only'}

    @app.get('/api/users')
    def users():
        return list(app.state.store.users.values())

    @app.get('/api/assets')
    def assets():
        return list(app.state.store.assets.values())

    @app.post('/api/analyze')
    def analysis(plan: QueryPlan):
        try:
            return analyze(app.state.store, plan)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post('/api/chat')
    async def conversation(request: ChatRequest):
        try:
            async with app.state.model_lock:
                return await chat(app.state.store, request)
        except ModelUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        except (ValueError, ValidationError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get('/api/history/{user_id}')
    def history(user_id: str, thread_id: str | None = None):
        try:
            with app.state.store.lock:
                return app.state.store.history(user_id, thread_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get('/api/evidence/{record_id}')
    def evidence(record_id: str):
        try:
            return app.state.store.source_record(record_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get('/api/document')
    def document(path: str):
        root = app.state.store.source
        target = (root / path).resolve()
        if not target.is_relative_to(root) or target.suffix != '.md' or not target.is_file():
            raise HTTPException(404, 'Document not found')
        return {'path': path, 'text': target.read_text(encoding='utf-8')}

    @app.post('/api/knowledge')
    def knowledge(command: KnowledgeCommand):
        try:
            return app.state.store.commit(command)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get('/api/knowledge')
    def ledger():
        s = app.state.store
        with s.lock:
            return [{'version': r['version'], 'created_at': r['created_at'], **json.loads(r['payload'])} for r in s.sql.execute('SELECT * FROM events ORDER BY version DESC LIMIT 100')]

    @app.post('/agent')
    async def ag_ui(body: dict):
        """AG-UI event stream consumed by CopilotKit's HttpAgent."""
        async def events():
            thread = body.get('threadId', 'default')
            run = body.get('runId', str(uuid4()))
            def event(kind, **fields):
                return 'data: ' + json.dumps({'type': kind, **fields}) + '\n\n'
            yield event('RUN_STARTED', threadId=thread, runId=run)
            try:
                forwarded = body.get('forwardedProps') or {}
                user_id = forwarded.get('user_id') or body.get('state', {}).get('user_id')
                if not user_id:
                    # Thread IDs are namespaced by the demo user, never taken from model output.
                    user_id = thread.split(':', 1)[0]
                user_messages = [m for m in body.get('messages', []) if m.get('role') == 'user']
                if not user_messages:
                    yield event('RUN_FINISHED', threadId=thread, runId=run)
                    return
                parts = thread.split(':')
                thread_date = parts[1] if len(parts) >= 3 else '2026-10-16'
                request = ChatRequest(user_id=user_id, thread_id=thread, message=user_messages[-1]['content'], as_of=forwarded.get('as_of', thread_date))
                async with app.state.model_lock:
                    result = await chat(app.state.store, request)
                message_id, tool_id = str(uuid4()), str(uuid4())
                yield event('TEXT_MESSAGE_START', messageId=message_id, role='assistant')
                yield event('TEXT_MESSAGE_CONTENT', messageId=message_id, delta=result['answer'])
                yield event('TEXT_MESSAGE_END', messageId=message_id)
                yield event('TOOL_CALL_START', toolCallId=tool_id, toolCallName='show_analysis', parentMessageId=message_id)
                yield event('TOOL_CALL_ARGS', toolCallId=tool_id, delta=json.dumps({'title': result['operation']}))
                yield event('TOOL_CALL_END', toolCallId=tool_id)
                yield event('TOOL_CALL_RESULT', messageId=str(uuid4()), toolCallId=tool_id, content=json.dumps(result), role='tool')
                yield event('RUN_FINISHED', threadId=thread, runId=run)
            except Exception as exc:
                message = str(exc) if isinstance(exc, (ModelUnavailable, ValueError)) else 'Analysis failed. Check the backend logs and retry.'
                yield event('RUN_ERROR', message=message)
        return StreamingResponse(events(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache'})

    return app


app = create_app()
