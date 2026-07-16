import asyncio
import json

from fastapi import WebSocket

class ConnectionManager:
    def __init__(self):
        self.active: dict[str, WebSocket] = {}
        self.buffer: dict[str, list] = {}  # last 10 events per project
        self.loops: dict[str, asyncio.AbstractEventLoop] = {}
        self._seq = 0

    def _stamp(self, event: dict) -> dict:
        # Give every fresh event a unique seq so two otherwise-identical events
        # (e.g. gate_reached for the same gate after a feedback re-run) are
        # distinguishable client-side. Buffered events resent on reconnect keep
        # their original seq, so the client can still dedupe redeliveries.
        if event.get("type") == "heartbeat" or "seq" in event:
            return event
        self._seq += 1
        return {**event, "seq": self._seq}

    async def connect(self, project_id: str, ws: WebSocket):
        await ws.accept()
        self.active[project_id] = ws
        self.loops[project_id] = asyncio.get_running_loop()
        # Send buffered events to reconnecting client
        for event in self.buffer.get(project_id, []):
            await ws.send_text(json.dumps(event))

    def disconnect(self, project_id: str):
        self.active.pop(project_id, None)
        self.loops.pop(project_id, None)

    async def broadcast(self, project_id: str, event: dict):
        event = self._stamp(event)
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

    def broadcast_sync(self, project_id: str, event: dict):
        """
        Sync-safe broadcast for use inside synchronous agent functions.
        Uses the running event loop if available, otherwise creates a new one.
        """
        event = self._stamp(event)
        if event.get("type") != "heartbeat":
            if project_id not in self.buffer:
                self.buffer[project_id] = []
            self.buffer[project_id].append(event)
            self.buffer[project_id] = self.buffer[project_id][-10:]

        ws = self.active.get(project_id)
        if not ws:
            return

        try:
            loop = self.loops.get(project_id)
            if loop and loop.is_running():
                future = asyncio.run_coroutine_threadsafe(ws.send_text(json.dumps(event)), loop)
                future.result(timeout=5)
            else:
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(ws.send_text(json.dumps(event)))
                finally:
                    loop.close()
        except Exception as e:
            print(f"[WebSocket] Broadcast failed for {project_id}: {e}")
            self.disconnect(project_id)

manager = ConnectionManager()  # singleton
