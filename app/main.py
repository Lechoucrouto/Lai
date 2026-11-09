import os
import json
import asyncio
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
from arq import create_pool
from arq.connections import RedisSettings
from redis.asyncio import Redis
from pydantic import BaseModel, HttpUrl

app = FastAPI(title="Autonomous AI Agent & Heavy RAG API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

class AgentRequest(BaseModel):
    query: str
    seed_urls: list[str]

@app.get("/healthz")
async def health():
    return {"status": "ok", "service": "agent-api"}

@app.post("/api/agent/run")
async def run_agent_job(req: AgentRequest):
    """Enqueues heavy agent pipeline to Render background worker."""
    from urllib.parse import urlparse
    u = urlparse(REDIS_URL)
    redis_settings = RedisSettings(
        host=u.hostname or "localhost",
        port=u.port or 6379,
        password=u.password,
        ssl=u.scheme == "rediss"
    )
    
    redis_pool = await create_pool(redis_settings)
    job = await redis_pool.enqueue_job("process_agent_task", job_id=None, query=req.query, seed_urls=[str(u) for u in req.seed_urls])
    
    return {
        "status": "queued",
        "job_id": job.job_id,
        "stream_url": f"/api/agent/stream/{job.job_id}"
    }

@app.get("/api/agent/stream/{job_id}")
async def stream_agent_updates(job_id: str):
    """Streams live execution thoughts & results via SSE."""
    async def event_generator():
        redis = Redis.from_url(REDIS_URL, decode_responses=True)
        pubsub = redis.pubsub()
        await pubsub.subscribe(f"job:{job_id}")

        try:
            yield {
                "event": "connected",
                "data": json.dumps({"message": f"Listening to job {job_id}"})
            }

            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message:
                    data = json.loads(message["data"])
                    yield {
                        "event": "update",
                        "data": json.dumps(data)
                    }
                    if data.get("step") in ["final_result", "agent_error"]:
                        break
                await asyncio.sleep(0.1)
        finally:
            await pubsub.unsubscribe(f"job:{job_id}")
            await redis.aclose()

    return EventSourceResponse(event_generator())