# SillyTavern Extension - ChatBridge

A SillyTavern browser extension that exposes SillyTavern's conversation engine as a local REST API. External tools (scripts, bots, automation pipelines) can send messages, maintain session history, and receive character responses — all through a simple HTTP interface, without touching SillyTavern's UI directly.

Originally forked from [ldc861117/SillyTavern-Extension-ChatBridge](https://github.com/ldc861117/SillyTavern-Extension-ChatBridge), then substantially rewritten and extended.

---

## Architecture

```
External Tool
    │
    │  POST /v1/message  (port 8003)
    ▼
ChatBridge Python Server  ──WebSocket (8001)──►  ST Browser Extension
                                                         │
                                              SillyTavern processes message
                                                         │
                                              MutationObserver detects response
                                                         │
                                              st_response via WebSocket
    ▲                                                    │
    └────────────────────────────────────────────────────┘
    { "reply": "..." }
```

The extension uses a **MutationObserver** on the `#chat` DOM element to detect when SillyTavern finishes generating a response, then routes it back to the waiting caller via WebSocket. This replaces the original streaming-intercept approach and is significantly more reliable.

---

## Components

| File | Role |
|---|---|
| `index.js` | ST browser extension — WebSocket client, chat injection, response observer |
| `ChatBridge_APIHijackForwarder.py` | Python server — REST endpoints, WebSocket bridge, session management |
| `settings.json` | Runtime configuration (copy from `settings.json.template`) |
| `start_forwarder.sh` | Startup script with dependency and port checks (Linux/macOS) |
| `start_forwarder.command` | macOS double-click launcher |

---

## Installation

### 1. Install the ST extension

Clone or copy this repo into SillyTavern's third-party extensions folder:

```bash
cd /path/to/SillyTavern/public/scripts/extensions/third-party/
git clone https://github.com/ewald1976/SillyTavern-Extension-ChatBridge
```

### 2. Configure

```bash
cp settings.json.template settings.json
```

Edit `settings.json`:

```json
{
    "websocket":   { "host": "localhost", "port": 8001 },
    "llm_api":     { "base_url": "http://localhost:1234/v1", "api_keys": [""] },
    "st_api":      { "host": "localhost", "port": 8002, "api_key": "st-internal-key" },
    "user_api":    { "host": "localhost", "port": 8003, "api_key": "your-secret-key" },
    "default_character": "YourCharacterName",
    "default_user": "",
    "stream": true
}
```

`st_api.port` must match the port ST is configured to use for its built-in API.  
`stream` must match the streaming setting in SillyTavern's API connection.

### 3. Install Python dependencies

```bash
pip install aiohttp websockets
```

Or use the startup script which handles this automatically:

```bash
bash start_forwarder.sh
```

### 4. Connect in SillyTavern

Open SillyTavern → Extensions → Chat Bridge. Set host/port to match `websocket` in `settings.json`, click **Connect**.

---

## API Reference

All endpoints require `Authorization: Bearer <user_api.api_key>`.

### `POST /v1/message` — Send a message

Sends a message to SillyTavern, accumulates session history, returns the character's reply.

**Request:**
```json
{
    "message": "What is an MDM system?",
    "user": "Elmar"
}
```

- `message` *(required)* — the user's text
- `user` *(optional)* — display name shown in ST chat. Priority: request field → `default_user` in settings → ST's configured `name1` → `"user"`

**Response:**
```json
{ "reply": "An MDM (Mobile Device Management) system is..." }
```

---

### `POST /v1/message/reset` — Reset session

Clears the accumulated session history and re-selects the default character.

**Response:**
```json
{ "status": "ok", "cleared": 4 }
```

---

### `GET /v1/chat` — Get session history

Returns the current accumulated message history.

**Response:**
```json
{
    "messages": [
        { "role": "user", "content": "Hello" },
        { "role": "assistant", "content": "Hi! How can I help?" }
    ],
    "count": 2
}
```

---

### `POST /v1/chat/completions` — OpenAI-compatible endpoint

Full OpenAI-format chat completions pass-through. Useful if you want to point an existing OpenAI-compatible tool directly at ChatBridge.

```python
from openai import OpenAI
client = OpenAI(api_key="your-secret-key", base_url="http://localhost:8003/v1")
```

---

## Character Selection

Set `default_character` in `settings.json` to automatically select a character on startup and after each session reset.

You can also trigger character selection dynamically by sending a WebSocket message from the Python side (used internally by `POST /v1/message/reset`).

---

## Settings Reference

| Key | Description |
|---|---|
| `websocket.port` | Port for the WebSocket bridge between Python server and ST extension (default: 8001) |
| `st_api.port` | Port ST uses for its built-in API — Python server proxies LLM calls here (default: 8002) |
| `user_api.port` | Port external tools connect to (default: 8003) |
| `user_api.api_key` | Bearer token required for all `/v1/*` endpoints |
| `llm_api.base_url` | Base URL of the LLM backend (LM Studio, Ollama, etc.) |
| `llm_api.api_keys` | One or more API keys, rotated round-robin |
| `default_character` | Character name to auto-select on start and after reset |
| `default_user` | Fallback display name for the user in ST chat |
| `stream` | Must match ST's streaming setting (`true`/`false`) |

---

## Dependencies

- Python 3.8+
- SillyTavern (latest)
- `aiohttp`
- `websockets`

---

## License

AGPL-3.0 — see [LICENSE](LICENSE)