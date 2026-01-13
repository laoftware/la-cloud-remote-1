"""
LA Software Cloud Remote - Backend Server
A simple API for remote ARM/DISARM control of the Mac laptop alarm.
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import secrets
import time
from typing import Optional
import json
from pathlib import Path

app = FastAPI(title="LA Software Cloud Remote")

# Enable CORS for the phone web page
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

devices: dict = {}
MAX_QUEUE_SIZE = 5
DEVICE_EXPIRY_SECONDS = 86400
EVENTS_LOG_PATH = Path("events.log")


def generate_token(length: int = 32) -> str:
    return secrets.token_urlsafe(length)


def cleanup_old_devices():
    current_time = time.time()
    expired_ids = [
        device_id
        for device_id, data in devices.items()
        if current_time - data.get("last_seen", 0) > DEVICE_EXPIRY_SECONDS
    ]
    for device_id in expired_ids:
        del devices[device_id]


def append_usage_event(e: "UsageEvent") -> None:
    record = {
        "user_id": e.user_id,
        "event": e.event,
        "timestamp": e.timestamp,
        "received_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    line = json.dumps(record, ensure_ascii=False)
    with EVENTS_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


class RegisterRequest(BaseModel):
    device_id: str
    pin_hash: str
    session_token: Optional[str] = None


class RegisterResponse(BaseModel):
    device_token: str


class CommandRequest(BaseModel):
    device_id: str
    pin_hash: str
    command: str
    session_token: Optional[str] = None


class CommandResponse(BaseModel):
    status: str


class PollRequest(BaseModel):
    device_id: str
    device_token: str


class PollResponse(BaseModel):
    command: Optional[str]


class HealthResponse(BaseModel):
    status: str
    message: str


class UpdateSessionRequest(BaseModel):
    device_id: str
    device_token: str
    session_token: str


class UpdateSessionResponse(BaseModel):
    status: str


class UsageEvent(BaseModel):
    user_id: str
    event: str
    timestamp: str


class UsageEventResponse(BaseModel):
    ok: bool


@app.get("/", response_model=HealthResponse)
async def health_check():
    return {"status": "ok", "message": "LA server running"}


@app.post("/register", response_model=RegisterResponse)
async def register(request: RegisterRequest):
    cleanup_old_devices()
    if not request.device_id or len(request.device_id) < 8:
        raise HTTPException(status_code=400, detail="Invalid device_id")
    device_token = generate_token(32)
    devices[request.device_id] = {
        "pin_hash": request.pin_hash,
        "device_token": device_token,
        "session_token": request.session_token or "",
        "queue": [],
        "last_seen": time.time(),
    }
    return {"device_token": device_token}


@app.post("/command", response_model=CommandResponse)
async def command(request: CommandRequest):
    if request.device_id not in devices:
        raise HTTPException(status_code=404, detail="Device not found")
    device = devices[request.device_id]
    if request.pin_hash != device["pin_hash"]:
        raise HTTPException(status_code=403, detail="Invalid PIN")
    stored_session = device.get("session_token", "")
    if stored_session and request.session_token != stored_session:
        raise HTTPException(status_code=410, detail="Session expired. Scan new QR code.")
    if request.command not in ["ARM", "DISARM"]:
        raise HTTPException(status_code=400, detail="Invalid command. Must be ARM or DISARM")
    if len(device["queue"]) >= MAX_QUEUE_SIZE:
        raise HTTPException(status_code=429, detail="Too many pending commands. Please wait.")
    device["queue"].append(request.command)
    return {"status": "ok"}


@app.post("/poll", response_model=PollResponse)
async def poll(request: PollRequest):
    if request.device_id not in devices:
        raise HTTPException(status_code=404, detail="Device not found")
    device = devices[request.device_id]
    if request.device_token != device["device_token"]:
        raise HTTPException(status_code=403, detail="Invalid device token")
    device["last_seen"] = time.time()
    if device["queue"]:
        cmd = device["queue"].pop(0)
        return {"command": cmd}
    return {"command": None}


@app.post("/update-session", response_model=UpdateSessionResponse)
async def update_session(request: UpdateSessionRequest):
    if request.device_id not in devices:
        raise HTTPException(status_code=404, detail="Device not found")
    device = devices[request.device_id]
    if request.device_token != device["device_token"]:
        raise HTTPException(status_code=403, detail="Invalid device token")
    device["session_token"] = request.session_token
    device["last_seen"] = time.time()
    return {"status": "ok"}


@app.post("/events", response_model=UsageEventResponse)
async def events(event: UsageEvent):
    allowed_events = {"armed", "disarmed", "alarm_fired"}
    if event.event not in allowed_events:
        raise HTTPException(status_code=400, detail="Invalid event type")
    append_usage_event(event)
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)
