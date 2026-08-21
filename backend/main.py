import os
import json
import time
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Bandwidth Config ---
BW_ACCOUNT_ID = os.getenv("BW_ACCOUNT_ID", "9903368")
BW_APPLICATION_ID = os.getenv("BW_APPLICATION_ID", "be8c0b73-910d-4b58-824d-4697850d4de9")
BW_CLIENT_ID = os.getenv("BW_CLIENT_ID", "CLI-e939c950-c418-4c42-9b30-01711063fe93")
BW_CLIENT_SECRET = os.getenv("BW_CLIENT_SECRET", "9OOpB83F8fMhGkcas0RbrwAGa9JanAVaCFKUQSr2exs")
BW_PHONE_NUMBER = os.getenv("BW_PHONE_NUMBER", "+19404060644")

# --- LLM Config (NVIDIA NIM) ---
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "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")
NVIDIA_API_URL = os.getenv("NVIDIA_API_URL", "https://integrate.api.nvidia.com/v1/chat/completions")
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "meta/llama-3.1-8b-instruct")

# --- Server Config ---
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")

SYSTEM_PROMPT = """You are a helpful, friendly AI voice assistant on a phone call.
Keep responses short - 1 or 2 sentences max.
Speak naturally and conversationally as if you're on a phone call.
No markdown, no bullet points, no special formatting."""

# In-memory conversation storage: call_id -> list of messages
conversation_history: dict[str, list[dict]] = {}

# In-memory call status tracking
active_calls: dict[str, dict] = {}


# ============================================================
# Bandwidth OAuth2 Token
# ============================================================

async def get_bw_token() -> str:
    """Get OAuth2 access token from Bandwidth using client credentials."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        res = await client.post(
            "https://auth.bandwidth.com/v1/oauth2/token",
            auth=(BW_CLIENT_ID, BW_CLIENT_SECRET),
            data={"grant_type": "client_credentials"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if res.status_code != 200:
            raise Exception(f"Failed to get Bandwidth token: {res.status_code} {res.text}")
        return res.json()["access_token"]


# ============================================================
# BXML Helpers
# ============================================================

def bxml_response(*verbs: str) -> str:
    """Wrap BXML verbs in a Response tag."""
    inner = "\n    ".join(verbs)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n    {inner}\n</Response>'


def bxml_speak(text: str, voice: str = "susan") -> str:
    return f'<SpeakSentence voice="{voice}">{text}</SpeakSentence>'


def bxml_gather(speech_url: str, speech_method: str = "POST", first_digit_timeout: int = 5,
                child_verb: str = None) -> str:
    children = f"\n        {child_verb}" if child_verb else ""
    return f'<Gather input="speech" gatherUrl="{speech_url}" gatherMethod="{speech_method}" firstDigitTimeout="{first_digit_timeout}">{children}\n    </Gather>'


def bxml_play_audio(url: str) -> str:
    return f"<PlayAudio>{url}</PlayAudio>"


def bxml_hangup() -> str:
    return "<Hangup/>"


# ============================================================
# LLM Integration
# ============================================================

async def ask_llm(user_text: str, call_id: str) -> str:
    """Send user text to LLM and get a response."""
    if call_id not in conversation_history:
        conversation_history[call_id] = []

    conversation_history[call_id].append({"role": "user", "content": user_text})

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + conversation_history[call_id]

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.post(
                NVIDIA_API_URL,
                headers={
                    "Authorization": f"Bearer {NVIDIA_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": NVIDIA_MODEL,
                    "messages": messages,
                    "max_tokens": 150,
                    "temperature": 0.7,
                },
            )
            if res.status_code == 200:
                reply = res.json()["choices"][0]["message"]["content"].strip()
                conversation_history[call_id].append({"role": "assistant", "content": reply})
                return reply
            else:
                print(f"LLM error: {res.status_code} {res.text[:200]}")
                return "I'm sorry, I'm having trouble thinking right now. Could you say that again?"
    except Exception as e:
        print(f"LLM exception: {e}")
        return "I'm sorry, I seem to be having a technical issue. Please try again."


# ============================================================
# Bandwidth Voice API
# ============================================================

async def bw_create_call(to_number: str) -> dict:
    """Create an outbound call via Bandwidth Voice API."""
    token = await get_bw_token()
    async with httpx.AsyncClient(timeout=30.0) as client:
        res = await client.post(
            f"https://voice.bandwidth.com/api/v2/accounts/{BW_ACCOUNT_ID}/calls",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "from": BW_PHONE_NUMBER,
                "to": to_number,
                "applicationId": BW_APPLICATION_ID,
                "answerUrl": f"{BASE_URL}/bandwidth/webhooks/voice/answer",
                "callStatusUrl": f"{BASE_URL}/bandwidth/webhooks/voice/status",
                "tag": to_number,
            },
        )
        if res.status_code not in (200, 201):
            raise Exception(f"Failed to create call: {res.status_code} {res.text}")
        return res.json()


async def bw_answer_call(call_id: str) -> dict:
    """Answer an inbound call (for pending calls)."""
    token = await get_bw_token()
    async with httpx.AsyncClient(timeout=30.0) as client:
        res = await client.post(
            f"https://voice.bandwidth.com/api/v2/accounts/{BW_ACCOUNT_ID}/calls/{call_id}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"state": "active"},
        )
        return res.json() if res.status_code == 200 else {}


async def bw_hangup_call(call_id: str) -> dict:
    """Hang up a call."""
    token = await get_bw_token()
    async with httpx.AsyncClient(timeout=30.0) as client:
        res = await client.post(
            f"https://voice.bandwidth.com/api/v2/accounts/{BW_ACCOUNT_ID}/calls/{call_id}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"state": "completed"},
        )
        return res.json() if res.status_code == 200 else {}


async def bw_get_calls() -> list:
    """Get active calls."""
    token = await get_bw_token()
    async with httpx.AsyncClient(timeout=30.0) as client:
        res = await client.get(
            f"https://voice.bandwidth.com/api/v2/accounts/{BW_ACCOUNT_ID}/calls",
            headers={"Authorization": f"Bearer {token}"},
        )
        if res.status_code == 200:
            return res.json()
        return []


# ============================================================
# Webhook Endpoints - Inbound Calls
# ============================================================

@app.post("/bandwidth/webhooks/voice/initiate")
async def webhook_initiate(request: Request):
    """Called when an inbound call is received. Returns BXML to greet and gather speech."""
    body = await request.json()
    call_id = body.get("callId", "")
    from_number = body.get("from", "unknown")
    to_number = body.get("to", BW_PHONE_NUMBER)

    print(f"INBOUND CALL: {from_number} -> {to_number} (callId={call_id})")

    # Track the call
    active_calls[call_id] = {
        "callId": call_id,
        "from": from_number,
        "to": to_number,
        "direction": "inbound",
        "status": "initiated",
        "startTime": time.time(),
    }

    # Initialize conversation history
    conversation_history[call_id] = []

    greeting = "Hello! I'm an AI assistant. How can I help you today?"
    gather_url = f"{BASE_URL}/bandwidth/webhooks/voice/gather"

    bxml = bxml_response(
        bxml_gather(gather_url, child_verb=bxml_speak(greeting))
    )

    return PlainTextResponse(content=bxml, media_type="application/xml")


@app.post("/bandwidth/webhooks/voice/answer")
async def webhook_answer(request: Request):
    """Called when an outbound call is answered. Returns BXML greeting + gather."""
    body = await request.json()
    call_id = body.get("callId", "")
    from_number = body.get("from", BW_PHONE_NUMBER)
    to_number = body.get("to", "unknown")

    print(f"CALL ANSWERED: {from_number} -> {to_number} (callId={call_id})")

    # Track the call
    active_calls[call_id] = {
        "callId": call_id,
        "from": from_number,
        "to": to_number,
        "direction": "outbound",
        "status": "answered",
        "startTime": time.time(),
    }

    # Initialize conversation history
    conversation_history[call_id] = []

    greeting = "Hello! I'm an AI assistant. How can I help you today?"
    gather_url = f"{BASE_URL}/bandwidth/webhooks/voice/gather"

    bxml = bxml_response(
        bxml_gather(gather_url, child_verb=bxml_speak(greeting))
    )

    return PlainTextResponse(content=bxml, media_type="application/xml")


@app.post("/bandwidth/webhooks/voice/gather")
async def webhook_gather(request: Request):
    """Called when speech is gathered. Sends to LLM and returns BXML response."""
    body = await request.json()
    call_id = body.get("callId", "")
    speech_text = body.get("transcription", {}).get("text", "") if isinstance(body.get("transcription"), dict) else body.get("speech", "")

    # Handle different Bandwidth event shapes for speech
    if not speech_text:
        speech_text = body.get("text", "")

    print(f"GATHER (callId={call_id}): speech='{speech_text}'")

    if not speech_text or not speech_text.strip():
        # No speech detected, ask again
        gather_url = f"{BASE_URL}/bandwidth/webhooks/voice/gather"
        bxml = bxml_response(
            bxml_gather(gather_url, child_verb=bxml_speak("I didn't catch that. Could you say that again?"))
        )
        return PlainTextResponse(content=bxml, media_type="application/xml")

    # Get LLM response
    reply = await ask_llm(speech_text.strip(), call_id)
    print(f"LLM REPLY (callId={call_id}): '{reply}'")

    # Update call status
    if call_id in active_calls:
        active_calls[call_id]["lastActivity"] = time.time()
        active_calls[call_id]["lastUserSpeech"] = speech_text.strip()
        active_calls[call_id]["lastAIReply"] = reply

    # Continue the conversation with another gather
    gather_url = f"{BASE_URL}/bandwidth/webhooks/voice/gather"

    bxml = bxml_response(
        bxml_speak(reply),
        bxml_gather(gather_url, child_verb=bxml_speak(" ")  # silent gather after reply
    )

    return PlainTextResponse(content=bxml, media_type="application/xml")


@app.post("/bandwidth/webhooks/voice/status")
async def webhook_status(request: Request):
    """Called when call status changes."""
    body = await request.json()
    call_id = body.get("callId", "")
    event = body.get("eventType", "")
    cause = body.get("cause", "")

    print(f"STATUS (callId={call_id}): event={event}, cause={cause}")

    if call_id in active_calls:
        active_calls[call_id]["status"] = event
        active_calls[call_id]["lastActivity"] = time.time()

    # Clean up ended calls
    if event in ("disconnect", "complete", "reject"):
        if call_id in active_calls:
            active_calls[call_id]["endTime"] = time.time()
        # Clean up conversation history after a delay
        if call_id in conversation_history:
            del conversation_history[call_id]

    return JSONResponse(content={"ok": True})


# ============================================================
# API Endpoints - Call Management
# ============================================================

@app.post("/api/calls")
async def api_create_call(request: Request):
    """Create an outbound call."""
    body = await request.json()
    to_number = body.get("to", "")

    if not to_number:
        raise HTTPException(status_code=400, detail="Missing 'to' phone number")

    try:
        result = await bw_create_call(to_number)
        return JSONResponse(content={"ok": True, "call": result})
    except Exception as e:
        print(f"CREATE CALL ERROR: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/calls/{call_id}/hangup")
async def api_hangup_call(call_id: str):
    """Hang up a call."""
    try:
        result = await bw_hangup_call(call_id)
        return JSONResponse(content={"ok": True, "result": result})
    except Exception as e:
        print(f"HANGUP ERROR: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/calls")
async def api_list_calls():
    """List active calls."""
    try:
        calls = await bw_get_calls()
        return JSONResponse(content={"ok": True, "calls": calls})
    except Exception as e:
        print(f"LIST CALLS ERROR: {e}")
        # Fall back to locally tracked calls
        return JSONResponse(content={"ok": True, "calls": list(active_calls.values())})


@app.get("/api/calls/active")
async def api_active_calls():
    """Get locally tracked active calls."""
    now = time.time()
    # Filter out calls older than 30 minutes
    recent = {
        cid: c for cid, c in active_calls.items()
        if now - c.get("lastActivity", c.get("startTime", 0)) < 1800
    }
    return JSONResponse(content={"ok": True, "calls": list(recent.values())})


@app.get("/api/config")
async def api_config():
    """Get public config for the frontend."""
    return JSONResponse(content={
        "phoneNumber": BW_PHONE_NUMBER,
        "accountId": BW_ACCOUNT_ID,
    })


# ============================================================
# Health Check
# ============================================================

@app.get("/health")
async def health():
    return {"status": "ok"}


# ============================================================
# Static Files (Frontend)
# ============================================================

app.mount("/", StaticFiles(directory="../frontend", html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
