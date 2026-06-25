import asyncio
from fastapi import APIRouter, WebSocket
from app.core.connection_manager import manager

router = APIRouter(tags=["websocket"])

@router.websocket("/ws/projects/{project_id}")
async def ws_endpoint(websocket: WebSocket, project_id: str):
    await manager.connect(project_id, websocket)
    try:
        while True:
            await asyncio.sleep(15)
            await manager.broadcast(project_id, {"type": "heartbeat"})
    except Exception:
        manager.disconnect(project_id)
