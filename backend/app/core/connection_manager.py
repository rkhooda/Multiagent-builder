from fastapi import WebSocket
import json

class ConnectionManager:
    def __init__(self):
        self.active: dict[str, WebSocket] = {}
        self.buffer: dict[str, list] = {}  # last 10 events per project

    async def connect(self, project_id: str, ws: WebSocket):
        await ws.accept()
        self.active[project_id] = ws
        # Send buffered events to reconnecting client
        for event in self.buffer.get(project_id, []):
            await ws.send_text(json.dumps(event))

    def disconnect(self, project_id: str):
        self.active.pop(project_id, None)

    async def broadcast(self, project_id: str, event: dict):
        # Buffer the event if it is not a heartbeat
        if event.get("type") != "heartbeat":
            if project_id not in self.buffer:
                self.buffer[project_id] = []
            self.buffer[project_id].append(event)
            self.buffer[project_id] = self.buffer[project_id][-10:]  # keep last 10
        # Send if client is connected
        ws = self.active.get(project_id)
        if ws:
            try:
                await ws.send_text(json.dumps(event))
            except Exception:
                self.disconnect(project_id)

manager = ConnectionManager()  # singleton
