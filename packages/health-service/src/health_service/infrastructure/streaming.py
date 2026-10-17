"""提供仅发布任务标识的 Redis Streams 适配器。"""

from django.conf import settings
from redis import Redis
from redis.exceptions import RedisError


def publish_task_message(task_id: int) -> bool:
    """向配置的 Stream 发布任务主键，Redis 错误保留数据库补发资格。"""
    if isinstance(task_id, bool) or not isinstance(task_id, int) or task_id <= 0:
        raise ValueError("任务标识无效")
    client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        client.xadd(settings.INFERENCE_STREAM, {"task_id": str(task_id)})
    except RedisError:
        return False
    finally:
        client.close()
    return True
