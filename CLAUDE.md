# StackFlow

## Restart Server After Backend Changes

**Whenever you modify any Python file** (`src/`, `modules/`, `*.py`) **or any prompt file** (`prompts/*.md`), restart the API server so changes take effect:

```bash
curl -X POST http://0.0.0.0:8000/restart
```

## Error Handling

**Never use try/except to silently swallow errors in nodes.** Let exceptions propagate so they surface as red glows in the UI and are directly retriable. If a node fails, `raise ValueError(...)` or `raise Exception(...)` instead of returning an error status or logging and continuing.

## Architecture

### Backend (Python / FastAPI)
- Entry point: `src/api_server.py`
- Node registry: `src/utils/setup/node_registry.py`
- Graph execution: `src/graphs/graph_factory.py`
- Database: PostgreSQL via `src/utils/setup/db.py`
- Observability: Langfuse

### Frontend (LiteGraph)
- Entry point: `litegraph-editor/main.js`
- Components: `litegraph-editor/components/`

### Package Manager
- Modules live in `modules/{name}/` — each has `manifest.json`, `nodes/`
- Core nodes (always loaded): `src/nodes/common/`, `src/nodes/abstract/`
