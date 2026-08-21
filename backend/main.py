import os
import uuid
import time
import asyncio
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, PlainTextResponse, Response
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

# --- Fish Audio TTS Config ---
FISH_API_KEY = os.getenv("FISH_API_KEY", "sk-fish-8FJcUwnKbh4fbVZisgkOYQ4n8Hsaewl8bMAfCpeGI2U")
FISH_REFERENCE_ID = os.getenv("FISH_REFERENCE_ID", "ca6a0e466ed34d2ba98dcde5b24d8cc8")

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

# In-memory stores
conversation_history: dict[str, list[dict]] = {}
active_calls: dict[str, dict] = {}
audio_files: dict[str, bytes] = {}  # filename -> audio bytes


# ============================================================
# Bandwidth OAuth2 Token
# ============================================================

async def get_bw_token() -> str:
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
# Fish Audio TTS
# ============================================================

async def fish_tts(text: str) -> bytes:
    """Generate speech audio from text using Fish Audio TTS."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.post(
                "https://api.fish.audio/v1/tts",
                headers={
                    "Authorization": f"Bearer {FISH_API_KEY}",
                    "Content-Type": "application/json",
                    "model": "s2.1-pro-free",
                },
                json={
                    "text": text,
                    "reference_id": FISH_REFERENCE_ID,
                    "format": "mp3",
                },
            )
            if res.status_code == 200 and len(res.content) > 100:
                print(f"TTS OK: {len(res.content)} bytes for '{text[:50]}...'")
                return res.content
            else:
                print(f"TTS ERROR: {res.status_code}")
                return b""
    except Exception as e:
        print(f"TTS EXCEPTION: {e}")
        return b""


def store_audio(audio_data: bytes) -> str:
    """Store audio and return a filename."""
    filename = f"{uuid.uuid4().hex}.mp3"
    audio_files[filename] = audio_data
    return filename


# ============================================================
# BXML Helpers
# ============================================================

def bxml_response(*verbs: str) -> str:
    inner = "\n    ".join(verbs)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n    {inner}\n</Response>'


def bxml_play_audio(url: str) -> str:
    return f"<PlayAudio>{url}</PlayAudio>"


def bxml_pause(seconds: int = 1) -> str:
    return f"<Pause duration=\"{seconds}\"/>"


def bxml_gather(speech_url: str, speech_method: str = "POST",
                first_digit_timeout: int = 5, child_verb: str = None) -> str:
    children = f"\n        {child_verb}" if child_verb else ""
    return (
        f'<Gather input="speech" gatherUrl="{speech_url}" '
        f'gatherMethod="{speech_method}" firstDigitTimeout="{first_digit_timeout}">'
        f'{children}\n    </Gather>'
    )


def bxml_hangup() -> str:
    return "<Hangup/>"


# ============================================================
# LLM Integration
# ============================================================

async def ask_llm(user_text: str, call_id: str) -> str:
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


async def bw_hangup_call(call_id: str) -> dict:
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
    token = await get_bw_token()
    async with httpx.AsyncClient(timeout=30.0) as client:
        res = await client.get(
            f"https://voice.bandwidth.com/api/v2/accounts/{BW_ACCOUNT_ID}/calls",
            headers={"Authorization": f"Bearer {token}"},
        )
        return res.json() if res.status_code == 200 else []


# ============================================================
# Helper: Generate greeting BXML with Fish Audio TTS
# ============================================================

async def make_greeting_bxml(call_id: str) -> str:
    """Generate greeting audio via Fish TTS and return BXML with PlayAudio + Gather."""
    greeting = "Hello! I'm an AI assistant. How can I help you today?"
    audio_data = await fish_tts(greeting)
    gather_url = f"{BASE_URL}/bandwidth/webhooks/voice/gather"

    if audio_data:
        filename = store_audio(audio_data)
        audio_url = f"{BASE_URL}/audio/{filename}"
        return bxml_response(
            bxml_play_audio(audio_url),
            bxml_pause(1),
            bxml_gather(gather_url),
        )
    else:
        # Fallback: no audio generated, just gather
        return bxml_response(
            bxml_gather(gather_url),
        )


# ============================================================
# Webhook Endpoints
# ============================================================

@app.post("/bandwidth/webhooks/voice/initiate")
async def webhook_initiate(request: Request):
    """Inbound call received."""
    body = await request.json()
    call_id = body.get("callId", "")
    from_number = body.get("from", "unknown")
    to_number = body.get("to", BW_PHONE_NUMBER)

    print(f"INBOUND: {from_number} -> {to_number} (callId={call_id})")

    active_calls[call_id] = {
        "callId": call_id,
        "from": from_number,
        "to": to_number,
        "direction": "inbound",
        "status": "initiated",
        "startTime": time.time(),
    }
    conversation_history[call_id] = []

    bxml = await make_greeting_bxml(call_id)
    return PlainTextResponse(content=bxml, media_type="application/xml")


@app.post("/bandwidth/webhooks/voice/answer")
async def webhook_answer(request: Request):
    """Outbound call answered."""
    body = await request.json()
    call_id = body.get("callId", "")
    from_number = body.get("from", BW_PHONE_NUMBER)
    to_number = body.get("to", "unknown")

    print(f"ANSWERED: {from_number} -> {to_number} (callId={call_id})")

    active_calls[call_id] = {
        "callId": call_id,
        "from": from_number,
        "to": to_number,
        "direction": "outbound",
        "status": "answered",
        "startTime": time.time(),
    }
    conversation_history[call_id] = []

    bxml = await make_greeting_bxml(call_id)
    return PlainTextResponse(content=bxml, media_type="application/xml")


@app.post("/bandwidth/webhooks/voice/gather")
async def webhook_gather(request: Request):
    """Speech gathered → LLM → Fish TTS → PlayAudio."""
    body = await request.json()
    call_id = body.get("callId", "")
    speech_text = ""

    # Handle different Bandwidth event shapes
    if isinstance(body.get("transcription"), dict):
        speech_text = body["transcription"].get("text", "")
    if not speech_text:
        speech_text = body.get("speech", "")
    if not speech_text:
        speech_text = body.get("text", "")

    print(f"GATHER (callId={call_id}): '{speech_text}'")

    if not speech_text or not speech_text.strip():
        # No speech detected
        audio_data = await fish_tts("I didn't catch that. Could you say that again?")
        gather_url = f"{BASE_URL}/bandwidth/webhooks/voice/gather"
        if audio_data:
            filename = store_audio(audio_data)
            audio_url = f"{BASE_URL}/audio/{filename}"
            bxml = bxml_response(
                bxml_play_audio(audio_url),
                bxml_pause(1),
                bxml_gather(gather_url),
            )
        else:
            bxml = bxml_response(bxml_gather(gather_url))
        return PlainTextResponse(content=bxml, media_type="application/xml")

    # Get LLM reply
    reply = await ask_llm(speech_text.strip(), call_id)
    print(f"LLM REPLY: '{reply}'")

    # Update call tracking
    if call_id in active_calls:
        active_calls[call_id]["lastActivity"] = time.time()
        active_calls[call_id]["lastUserSpeech"] = speech_text.strip()
        active_calls[call_id]["lastAIReply"] = reply

    # Generate TTS audio for the reply
    audio_data = await fish_tts(reply)
    gather_url = f"{BASE_URL}/bandwidth/webhooks/voice/gather"

    if audio_data:
        filename = store_audio(audio_data)
        audio_url = f"{BASE_URL}/audio/{filename}"
        bxml = bxml_response(
            bxml_play_audio(audio_url),
            bxml_pause(1),
            bxml_gather(gather_url),
        )
    else:
        # Fallback: just gather again
        bxml = bxml_response(bxml_gather(gather_url))

    return PlainTextResponse(content=bxml, media_type="application/xml")


@app.post("/bandwidth/webhooks/voice/status")
async def webhook_status(request: Request):
    body = await request.json()
    call_id = body.get("callId", "")
    event = body.get("eventType", "")
    cause = body.get("cause", "")

    print(f"STATUS (callId={call_id}): event={event}, cause={cause}")

    if call_id in active_calls:
        active_calls[call_id]["status"] = event
        active_calls[call_id]["lastActivity"] = time.time()

    if event in ("disconnect", "complete", "reject"):
        if call_id in active_calls:
            active_calls[call_id]["endTime"] = time.time()
        if call_id in conversation_history:
            del conversation_history[call_id]

    return JSONResponse(content={"ok": True})


# ============================================================
# Audio Serving Endpoint
# ============================================================

@app.get("/audio/{filename}")
async def serve_audio(filename: str):
    """Serve generated TTS audio files to Bandwidth."""
    if filename not in audio_files:
        return Response(status_code=404)
    audio_data = audio_files.pop(filename)  # serve once and delete
    return Response(content=audio_data, media_type="audio/mpeg")


# ============================================================
# API Endpoints
# ============================================================

@app.post("/api/calls")
async def api_create_call(request: Request):
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
    try:
        result = await bw_hangup_call(call_id)
        return JSONResponse(content={"ok": True, "result": result})
    except Exception as e:
        print(f"HANGUP ERROR: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/calls")
async def api_list_calls():
    try:
        calls = await bw_get_calls()
        return JSONResponse(content={"ok": True, "calls": calls})
    except Exception as e:
        print(f"LIST CALLS ERROR: {e}")
        return JSONResponse(content={"ok": True, "calls": list(active_calls.values())})


@app.get("/api/calls/active")
async def api_active_calls():
    now = time.time()
    recent = {
        cid: c for cid, c in active_calls.items()
        if now - c.get("lastActivity", c.get("startTime", 0)) < 1800
    }
    return JSONResponse(content={"ok": True, "calls": list(recent.values())})


@app.get("/api/config")
async def api_config():
    return JSONResponse(content={
        "phoneNumber": BW_PHONE_NUMBER,
        "accountId": BW_ACCOUNT_ID,
    })


@app.get("/health")
async def health():
    return {"status": "ok"}


# ============================================================
# Static Files
# ============================================================

app.mount("/", StaticFiles(directory="../frontend", html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
