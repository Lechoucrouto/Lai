import os
import json
import asyncio
from arq import create_pool
from arq.connections import RedisSettings
from redis.asyncio import Redis
from app.agent import HeavyAgentRAGPipeline

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# Parse Redis connection string
def parse_redis_settings(url: str) -> RedisSettings:
    from urllib.parse import urlparse
    u = urlparse(url)
    return RedisSettings(
        host=u.hostname or "localhost",
        port=u.port or 6379,
        password=u.password,
        ssl=u.scheme == "rediss"
    )

async def process_agent_task(ctx, job_id: str, query: str, seed_urls: list[str]):
    """Job handler run by Render Background Worker."""
    redis_conn: Redis = ctx["redis_pubsub"]

    async def publish_status(step_name: str, status: str, payload: dict):
        event = {
            "job_id": job_id,
            "step": step_name,
            "status": status,
            "payload": payload
        }
        await redis_conn.publish(f"job:{job_id}", json.dumps(event))

    pipeline = HeavyAgentRAGPipeline(publish_callback=publish_status)
    try:
        result = await pipeline.run(query=query, seed_urls=seed_urls)
        await publish_status("final_result", "completed", {"output": result})
    except Exception as e:
        await publish_status("agent_error", "failed", {"error": str(e)})

async def startup(ctx):
    ctx["redis_pubsub"] = Redis.from_url(REDIS_URL, decode_responses=True)

async def shutdown(ctx):
    await ctx["redis_pubsub"].aclose()

class WorkerSettings:
    functions = [process_agent_task]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = parse_redis_settings(REDIS_URL)
    max_jobs = 10  # Max concurrent heavy jobs per worker instance