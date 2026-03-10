"""
sequenceDiagram
    participant User as External Application
    participant UserAPI as User Interface (Port 8003)
    participant WS as WebSocket (Port 8001)
    participant ST as SillyTavern
    participant STAPI as ST Interface (Port 8002)
    participant LLMAPI as LLM Interface
    participant LLM as External LLM

    User->>UserAPI: 1. Call API (OpenAI format)
    UserAPI->>WS: 2. Forward request via WebSocket
    WS->>ST: 3. Notify ST to process request
    ST->>STAPI: 4. ST calls its API after processing
    STAPI->>LLMAPI: 5. Forward to LLM interface
    LLMAPI->>LLM: 6. Call external LLM
    LLM-->>LLMAPI: 7. Return response
    LLMAPI-┬->>STAPI: 8a. Forward response to ST
           └->>UserAPI: 8b. Forward response to User API simultaneously
    STAPI-->ST: 9a. Return to ST
    UserAPI-->>User: 9b. Return to user
"""

import asyncio
import json
import logging
import os
import uuid
from collections import deque
from typing import Any, Dict, List

import aiohttp
import websockets
from aiohttp import web

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class APIKeyRotator:
    """Rotates through a list of API keys in round-robin fashion."""

    def __init__(self, api_keys: List[str]):
        self.api_keys = deque(api_keys)

    def get_next_key(self) -> str:
        current_key = self.api_keys[0]
        self.api_keys.rotate(-1)
        return current_key


class ChatBridgeForwarder:
    def __init__(self, settings_path: str):
        with open(settings_path, "r") as f:
            self.settings = json.load(f)

        self.ws_clients = set()
        self.key_rotator = APIKeyRotator(self.settings["llm_api"]["api_keys"])
        self.response_futures = {}
        self.session_history: List[Dict[str, str]] = []  # Accumulated chat history for /v1/message
        self.default_character: str = self.settings.get("default_character", "")
        self.use_stream: bool = self.settings.get("stream", False)

    async def start(self):
        # Start WebSocket server
        ws_server = websockets.serve(
            self.handle_websocket,
            self.settings["websocket"]["host"],
            self.settings["websocket"]["port"],
        )

        # Create ST API server (intercepts ST's outgoing LLM calls)
        st_app = web.Application()
        st_app.router.add_get("/models", self.handle_models)
        st_app.router.add_get("/v1/models", self.handle_models)
        st_app.router.add_post("/chat/completions", self.handle_chat_completions)
        st_app.router.add_post("/v1/chat/completions", self.handle_chat_completions)

        st_runner = web.AppRunner(st_app)
        await st_runner.setup()
        st_site = web.TCPSite(
            st_runner, self.settings["st_api"]["host"], self.settings["st_api"]["port"]
        )

        # Create User API server (external apps connect here)
        user_app = web.Application()
        user_app.router.add_post("/v1/chat/completions", self.handle_user_api)
        user_app.router.add_post("/v1/message", self.handle_message)
        user_app.router.add_post("/v1/message/reset", self.handle_message_reset)
        user_app.router.add_get("/v1/chat", self.handle_get_chat)
        user_runner = web.AppRunner(user_app)
        await user_runner.setup()
        user_site = web.TCPSite(
            user_runner,
            self.settings["user_api"]["host"],
            self.settings["user_api"]["port"],
        )

        # Start all servers
        await asyncio.gather(ws_server, st_site.start(), user_site.start())

        logger.info(
            f"WebSocket server running at ws://{self.settings['websocket']['host']}:{self.settings['websocket']['port']}"
        )
        logger.info(
            f"ST API server running at http://{self.settings['st_api']['host']}:{self.settings['st_api']['port']}"
        )
        logger.info(
            f"User API server running at http://{self.settings['user_api']['host']}:{self.settings['user_api']['port']}"
        )

    async def handle_websocket(self, websocket):
        """Handle incoming WebSocket connections from the ST browser extension."""
        self.ws_clients.add(websocket)
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    logger.info(f"Received WebSocket message: {data}")

                    # Handle ST response coming back through WebSocket
                    if data.get("type") == "st_response":
                        request_id = data.get("id")
                        if request_id in self.response_futures:
                            future = self.response_futures[request_id]
                            if not future.done():
                                future.set_result(data.get("content"))

                except json.JSONDecodeError:
                    logger.error("Invalid WebSocket message format")
        finally:
            self.ws_clients.remove(websocket)

    async def handle_user_api(self, request: web.Request) -> web.Response:
        """Handle incoming API requests from external applications."""
        if (
            request.headers.get("Authorization")
            != f"Bearer {self.settings['user_api']['api_key']}"
        ):
            return web.Response(status=401)

        try:
            request_data = await request.json()
            request_id = str(uuid.uuid4())
            is_stream = request_data.get("stream", False)
            logger.info(f"User API request ID={request_id}, stream={is_stream}")

            if is_stream:
                # Create streaming response
                stream_response = web.StreamResponse(
                    status=200,
                    headers={
                        "Content-Type": "text/event-stream",
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                    },
                )
                await stream_response.prepare(request)

                # Create event queue for streaming chunks
                queue = asyncio.Queue()
                self.response_futures[request_id] = queue

                try:
                    ws_message = {
                        "type": "user_request",
                        "id": request_id,
                        "content": request_data,
                    }

                    if not self.ws_clients:
                        return web.Response(
                            status=503, text="No WebSocket clients connected"
                        )

                    for ws in self.ws_clients:
                        try:
                            await ws.send(json.dumps(ws_message))
                            logger.info(
                                f"Request forwarded to WebSocket: ID={request_id}"
                            )
                            break
                        except Exception as e:
                            logger.error(f"Failed to send WebSocket message: {e}")
                            continue

                    # Wait for and forward response chunks
                    while True:
                        try:
                            chunk = await asyncio.wait_for(queue.get(), timeout=60.0)

                            if chunk and isinstance(chunk, str):
                                chunk = chunk.strip()
                                if not chunk:
                                    continue

                                if chunk == "[DONE]":
                                    await stream_response.write(b"data: [DONE]\n\n")
                                    logger.info(
                                        f"Sent stream end marker: ID={request_id}"
                                    )
                                    break

                                if not chunk.startswith("data: "):
                                    chunk = f"data: {chunk}"
                                if not chunk.endswith("\n\n"):
                                    chunk = f"{chunk}\n\n"

                                logger.debug(f"Sending response chunk: {chunk.strip()}")
                                await stream_response.write(chunk.encode())

                        except asyncio.TimeoutError:
                            logger.warning(
                                f"Timeout waiting for response chunk: ID={request_id}"
                            )
                            await stream_response.write(b"data: [DONE]\n\n")
                            break

                    return stream_response

                finally:
                    self.response_futures.pop(request_id, None)

            else:
                # Handle non-streaming request
                future = asyncio.Future()
                self.response_futures[request_id] = future

                ws_message = {
                    "type": "user_request",
                    "id": request_id,
                    "content": request_data,
                }

                if not self.ws_clients:
                    return web.Response(
                        status=503, text="No WebSocket clients connected"
                    )

                for ws in self.ws_clients:
                    try:
                        await ws.send(json.dumps(ws_message))
                        logger.info(f"Request forwarded to WebSocket: ID={request_id}")
                        break
                    except Exception as e:
                        logger.error(f"Failed to send WebSocket message: {e}")
                        continue

                try:
                    response = await asyncio.wait_for(future, timeout=60.0)
                    return web.json_response(response)
                finally:
                    self.response_futures.pop(request_id, None)

        except Exception as e:
            logger.error(f"Failed to handle user API request: {str(e)}", exc_info=True)
            return web.Response(status=500, text=f"Internal Server Error: {str(e)}")

    async def select_character(self, name: str) -> bool:
        """Send a select_character message to the ST extension via WebSocket."""
        if not name or not self.ws_clients:
            return False
        ws_message = {"type": "select_character", "name": name}
        for ws in self.ws_clients:
            try:
                await ws.send(json.dumps(ws_message))
                logger.info(f"Sent select_character: {name}")
                await asyncio.sleep(1.0)  # Give ST time to switch character
                return True
            except Exception as e:
                logger.error(f"Failed to send select_character: {e}")
        return False

    async def handle_message(self, request: web.Request) -> web.Response:
        """
        Simple text-in / text-out endpoint with session accumulation.
        POST /v1/message
        Body: { "message": "your text" }
        Returns: { "reply": "assistant response" }
        """
        if (
            request.headers.get("Authorization")
            != f"Bearer {self.settings['user_api']['api_key']}"
        ):
            return web.Response(status=401)

        try:
            body = await request.json()
            user_text = body.get("message", "").strip()
            if not user_text:
                return web.Response(status=400, text="Field 'message' is required")

            # Append user message to session history
            self.session_history.append({"role": "user", "content": user_text})
            logger.info(
                f"POST /v1/message — history length: {len(self.session_history)}"
            )

            # Build OpenAI-format payload from accumulated history
            request_data = {
                "model": "default",
                "stream": self.use_stream,
                "messages": self.session_history.copy(),
            }

            request_id = str(uuid.uuid4())
            ws_message = {
                "type": "user_request",
                "id": request_id,
                "content": request_data,
            }

            if not self.ws_clients:
                self.session_history.pop()  # Roll back on failure
                return web.Response(status=503, text="No WebSocket clients connected")

            if self.use_stream:
                # Streaming path: collect all chunks, reassemble full reply
                queue = asyncio.Queue()
                self.response_futures[request_id] = queue
                try:
                    for ws in self.ws_clients:
                        try:
                            await ws.send(json.dumps(ws_message))
                            logger.info(f"Message request forwarded to WebSocket: ID={request_id}")
                            break
                        except Exception as e:
                            logger.error(f"Failed to send WebSocket message: {e}")
                            continue

                    # Collect streaming chunks into full reply
                    full_reply = ""
                    while True:
                        try:
                            chunk = await asyncio.wait_for(queue.get(), timeout=60.0)
                            if chunk == "[DONE]":
                                break
                            # Parse SSE chunk: data: {json}
                            for line in chunk.splitlines():
                                line = line.strip()
                                if line.startswith("data: ") and line != "data: [DONE]":
                                    try:
                                        payload = json.loads(line[6:])
                                        delta = payload["choices"][0]["delta"].get("content", "")
                                        if delta:
                                            full_reply += delta
                                    except (json.JSONDecodeError, KeyError, IndexError):
                                        pass
                        except asyncio.TimeoutError:
                            logger.warning(f"Timeout waiting for stream chunk: ID={request_id}")
                            break

                    self.session_history.append({"role": "assistant", "content": full_reply})
                    logger.info(f"Session history now has {len(self.session_history)} messages")
                    return web.json_response({"reply": full_reply})

                finally:
                    self.response_futures.pop(request_id, None)

            else:
                # Non-streaming path
                future = asyncio.Future()
                self.response_futures[request_id] = future
                try:
                    for ws in self.ws_clients:
                        try:
                            await ws.send(json.dumps(ws_message))
                            logger.info(f"Message request forwarded to WebSocket: ID={request_id}")
                            break
                        except Exception as e:
                            logger.error(f"Failed to send WebSocket message: {e}")
                            continue

                    response = await asyncio.wait_for(future, timeout=60.0)

                    reply = ""
                    try:
                        reply = response["choices"][0]["message"]["content"]
                    except (KeyError, IndexError, TypeError):
                        logger.warning(f"Unexpected response structure: {response}")
                        reply = str(response)

                    self.session_history.append({"role": "assistant", "content": reply})
                    logger.info(f"Session history now has {len(self.session_history)} messages")
                    return web.json_response({"reply": reply})

                finally:
                    self.response_futures.pop(request_id, None)

        except Exception as e:
            logger.error(f"Failed to handle /v1/message: {str(e)}", exc_info=True)
            return web.Response(status=500, text=f"Internal Server Error: {str(e)}")

    async def handle_message_reset(self, request: web.Request) -> web.Response:
        """
        Clears the accumulated session history.
        POST /v1/message/reset
        Returns: { "status": "ok", "cleared": <n> }
        """
        if (
            request.headers.get("Authorization")
            != f"Bearer {self.settings['user_api']['api_key']}"
        ):
            return web.Response(status=401)

        cleared = len(self.session_history)
        self.session_history.clear()
        logger.info(f"Session history cleared ({cleared} messages removed)")

        # Re-select default character after reset
        if self.default_character:
            await self.select_character(self.default_character)

        return web.json_response({"status": "ok", "cleared": cleared})

    async def handle_get_chat(self, request: web.Request) -> web.Response:
        """
        Returns the current session history.
        GET /v1/chat
        Returns: { "messages": [...], "count": <n> }
        """
        if (
            request.headers.get("Authorization")
            != f"Bearer {self.settings['user_api']['api_key']}"
        ):
            return web.Response(status=401)

        return web.json_response(
            {"messages": self.session_history, "count": len(self.session_history)}
        )

    async def handle_models(self, request: web.Request) -> web.Response:
        """Handle model list requests - forwards to configured LLM API."""
        logger.info(f"Received models request: {request.path}")
        api_key = self.key_rotator.get_next_key()
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with aiohttp.ClientSession() as session:
                target_url = f"{self.settings['llm_api']['base_url']}/models"
                logger.info(f"Forwarding request to: {target_url}")
                async with session.get(target_url, headers=headers) as response:
                    response_data = await response.json()
                    logger.info(f"Models response: {response_data}")
                    return web.json_response(response_data)
        except Exception as e:
            logger.error(f"Failed to fetch model list: {str(e)}")
            return web.Response(status=500, text=str(e))

    async def handle_chat_completions(self, request: web.Request) -> web.Response:
        """
        Intercepts ST's outgoing chat completion requests.
        Forwards to LLM API and simultaneously returns response to waiting user requests.
        """
        try:
            request_data = await request.json()
            is_stream = request_data.get("stream", False)
            logger.info(
                f"Received chat completion request: PATH={request.path}, STREAM={is_stream}"
            )

            # Find active user requests waiting for a response
            active_user_futures = {
                rid: future
                for rid, future in self.response_futures.items()
                if not getattr(future, "done", lambda: True)()
            }

            if active_user_futures:
                logger.info(f"Found {len(active_user_futures)} active user request(s)")
            else:
                logger.warning("No active user requests found")

            api_key = self.key_rotator.get_next_key()
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            target_url = f"{self.settings['llm_api']['base_url']}/chat/completions"

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    target_url, json=request_data, headers=headers
                ) as llm_response:
                    if llm_response.headers.get("content-type") == "text/event-stream":
                        logger.info("Processing streaming response")
                        st_response = web.StreamResponse(
                            status=llm_response.status,
                            headers={"Content-Type": "text/event-stream"},
                        )
                        await st_response.prepare(request)

                        active_user_queues = {
                            rid: queue
                            for rid, queue in self.response_futures.items()
                            if isinstance(queue, asyncio.Queue)
                        }

                        async for chunk in llm_response.content:
                            if chunk:
                                chunk_str = chunk.decode()
                                logger.debug(f"Received chunk: {chunk_str[:100]}...")

                                # Forward to ST
                                await st_response.write(chunk)

                                # Forward to user queues simultaneously
                                if active_user_queues:
                                    for queue_id, queue in active_user_queues.items():
                                        try:
                                            await queue.put(chunk_str)
                                            logger.debug(
                                                f"Forwarded chunk to user queue {queue_id}"
                                            )
                                        except Exception as e:
                                            logger.error(
                                                f"Failed to forward to user queue {queue_id}: {e}"
                                            )

                        # Send end marker to all user queues
                        if active_user_queues:
                            for queue_id, queue in active_user_queues.items():
                                try:
                                    await queue.put("[DONE]")
                                    logger.info(
                                        f"Sent end marker to user queue {queue_id}"
                                    )
                                except Exception as e:
                                    logger.error(
                                        f"Failed to send end marker to {queue_id}: {e}"
                                    )

                        return st_response

                    else:
                        logger.info("Processing non-streaming response")
                        response_data = await llm_response.json()
                        logger.info(
                            f"Received LLM response: {str(response_data)[:200]}..."
                        )

                        # Forward to all waiting user requests
                        futures_updated = False
                        for request_id, future in list(active_user_futures.items()):
                            try:
                                if (
                                    isinstance(future, asyncio.Future)
                                    and not future.done()
                                ):
                                    future.set_result(response_data)
                                    logger.info(
                                        f"Successfully set result for user request: ID={request_id}"
                                    )
                                    futures_updated = True
                            except Exception as e:
                                logger.error(
                                    f"Failed to set result for {request_id}: {e}"
                                )

                        if not futures_updated:
                            logger.warning("No user request results were updated")

                        return web.json_response(
                            response_data, status=llm_response.status
                        )

        except Exception as e:
            error_msg = f"Failed to handle chat completion request: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return web.Response(status=500, text=error_msg)


async def main():
    settings_path = os.path.join(os.path.dirname(__file__), "settings.json")
    forwarder = ChatBridgeForwarder(settings_path)
    await forwarder.start()
    try:
        await asyncio.Future()  # Keep server running
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    asyncio.run(main())
