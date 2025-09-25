from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import time, json, uuid

from backend.chat_router import route_user_input

app = FastAPI()

class Message(BaseModel):
    role: str
    content: str

class CompletionRequest(BaseModel):
    model: str
    messages: List[Message]
    stream: Optional[bool] = False

    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    tools: Optional[List[Dict[str, Any]]] = None


@app.get("/ping")
async def ping():
    return {"status": "pon"}


@app.get("/v1/models")
async def list_models():

    model_id = "Audio Firmware Bot"
    now = int(time.time())
    return {
        "object": "list",
        "data": [
            {
                "id": model_id,
                "object": "model",
                "created": now,
                "owned_by": "you",
            }
        ]
    }


@app.post("/v1/chat/completions")
async def openai_compatible_chat(req: Request):
    body = await req.json()


    if "message" in body and "messages" not in body:
        body = {
            "model": body.get("model") or "Audio Firmware Bot",
            "messages": [{"role": "user", "content": body["message"]}],
            "stream": body.get("stream", False),
        }

    try:
        req_obj = CompletionRequest(**body)
        user_messages = [m.content for m in req_obj.messages if m.role == "user"]
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": f"bad_request: {e}"})

    if not user_messages:
        return JSONResponse(status_code=400, content={"error": "No user message provided"})

    last_message = user_messages[-1]
    answer = ""
    try:
        answer = route_user_input(last_message) or ""
    except Exception as e:
        answer = f"Internal Error: {e}"


    if not req_obj.stream:
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:10]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": req_obj.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": answer},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        }


    def sse_iter():
        chunk_id = f"chatcmpl-{uuid.uuid4().hex[:10]}"
        model = req_obj.model

        step = 30
        for i in range(0, len(answer), step):
            part = answer[i:i+step]
            data = {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": part},
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            time.sleep(0.01)
            
            
        done = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {"index": 0, "delta": {}, "finish_reason": "stop"}
            ],
        }
        yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse_iter(), media_type="text/event-stream")

@app.get("/api/system/info")
async def get_system_info():
    return {
        "status": "ok",
        "version": "0.1.0",
        "available_routes": ["/ping", "/v1/models", "/v1/chat/completions", "/api/chat"]
    }

@app.post("/api/chat")
async def legacy_api_chat(req: Request):
    return await openai_compatible_chat(req)