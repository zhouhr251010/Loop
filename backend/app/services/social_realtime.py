"""Realtime social-message fanout over SSE with Redis coordination."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncIterator

try:
    from redis.asyncio import Redis
    from redis.exceptions import RedisError
except ImportError:
    Redis = None

    class RedisError(Exception):
        """Fallback exception used when redis-py is unavailable."""


logger = logging.getLogger(__name__)

SOCIAL_EVENTS_CHANNEL_PREFIX = "loop:social_events:user:"
SOCIAL_EVENTS_PATTERN = f"{SOCIAL_EVENTS_CHANNEL_PREFIX}*"
SSE_HEARTBEAT_SECONDS = 15
RECENT_EVENT_DEDUPE_SECONDS = 60


class SocialRealtimeHub:
    """Fan out social notifications to local SSE clients and across workers."""

    def __init__(self) -> None:
        self._queues: dict[str, set[asyncio.Queue[str]]] = {}
        self._lock = asyncio.Lock()
        self._redis_client: Redis | None = None
        self._subscriber_task: asyncio.Task[None] | None = None
        self._redis_warning_logged = False
        self._recent_event_keys: dict[str, dict[str, float]] = {}

    async def stream(self, user_id: str) -> AsyncIterator[str]:
        """Yield server-sent event chunks for one authenticated user."""
        normalized_user_id = str(user_id).strip()
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
        async with self._lock:
            if normalized_user_id not in self._queues:
                self._queues[normalized_user_id] = set()
            self._queues[normalized_user_id].add(queue)
            self._ensure_redis_subscriber()

        logger.info("[Social SSE] user_id=%s connected", normalized_user_id)
        try:
            try:
                yield ": ready\n\n"
                while True:
                    try:
                        raw_message = await asyncio.wait_for(
                            queue.get(),
                            timeout=SSE_HEARTBEAT_SECONDS,
                        )
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    message = self._decode_redis_value(raw_message)
                    yield f"data: {message}\n\n"
            except asyncio.CancelledError:
                return
        finally:
            async with self._lock:
                queues = self._queues.get(normalized_user_id)
                if queues is not None:
                    queues.discard(queue)
                    if not queues:
                        del self._queues[normalized_user_id]
            logger.info("[Social SSE] user_id=%s disconnected", normalized_user_id)

    async def publish(self, user_id: str | int, message: str) -> bool:
        """Publish a JSON message to one target user."""
        delivered_user_ids = await self.publish_many([user_id], message)
        normalized_user_id = self._normalize_user_id(user_id)
        return normalized_user_id in delivered_user_ids

    async def publish_many(
        self,
        receiver_user_ids: list[str | int],
        message: str,
    ) -> list[str]:
        """Publish a JSON message to each target user."""
        normalized_user_ids = list(
            dict.fromkeys(
                normalized_user_id
                for user_id in receiver_user_ids
                if (normalized_user_id := self._normalize_user_id(user_id))
            ),
        )
        if not normalized_user_ids:
            return []

        delivered_user_ids = await self._deliver_local(normalized_user_ids, message)

        redis_client = self._get_redis_client()
        if redis_client is not None:
            try:
                for user_id in normalized_user_ids:
                    channel_name = self._channel_for_user(user_id)
                    await redis_client.publish(channel_name, message)
                return list(dict.fromkeys([*delivered_user_ids, *normalized_user_ids]))
            except (RedisError, OSError, ValueError) as exc:
                logger.warning(
                    "Redis social event publish failed; falling back to local fanout: %s",
                    exc,
                )

        return delivered_user_ids

    async def _deliver_local(self, user_ids: list[str], message: str) -> list[str]:
        delivered_user_ids: list[str] = []
        async with self._lock:
            queue_map = {
                user_id: list(self._queues.get(user_id, []))
                for user_id in dict.fromkeys(user_ids)
            }

        for user_id, queues in queue_map.items():
            message_key = self._message_key(message)
            if message_key and self._is_recent_duplicate(user_id, message_key):
                continue
            delivered = False
            for queue in queues:
                if queue.full():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                try:
                    queue.put_nowait(message)
                    delivered = True
                except asyncio.QueueFull:
                    logger.warning("[Social SSE] user_id=%s queue overflow", user_id)
            if delivered:
                if message_key:
                    self._remember_event_key(user_id, message_key)
                delivered_user_ids.append(user_id)
        return delivered_user_ids

    def _ensure_redis_subscriber(self) -> None:
        if self._subscriber_task is not None or self._get_redis_client() is None:
            return
        self._subscriber_task = asyncio.create_task(self._redis_subscribe_loop())

    async def _redis_subscribe_loop(self) -> None:
        redis_client = self._get_redis_client()
        if redis_client is None:
            return

        pubsub = redis_client.pubsub()
        try:
            await pubsub.psubscribe(SOCIAL_EVENTS_PATTERN)
            logger.info(
                "[Social SSE] Redis subscriber started pattern=%s",
                SOCIAL_EVENTS_PATTERN,
            )
            async for raw_message in pubsub.listen():
                message_type = self._decode_redis_value(raw_message.get("type"))
                if message_type not in {"pmessage", "message"}:
                    continue
                try:
                    raw_channel = raw_message.get("channel")
                    user_id = self._user_id_from_channel(raw_channel)
                    raw_data = raw_message.get("data")
                    message = self._decode_redis_value(raw_data)
                except (TypeError, ValueError) as exc:
                    logger.warning("Invalid Redis social event payload: %s", exc)
                    continue
                if user_id and message:
                    await self._deliver_local([user_id], message)
        except asyncio.CancelledError:
            raise
        except (RedisError, OSError, ValueError) as exc:
            logger.warning("[Social SSE] Redis subscriber stopped: %s", exc)
        finally:
            try:
                await pubsub.punsubscribe(SOCIAL_EVENTS_PATTERN)
                await pubsub.close()
            except Exception:
                pass
            self._subscriber_task = None

    def _get_redis_client(self) -> Redis | None:
        if Redis is None:
            self._warn_redis_unavailable(
                "[WARN] redis-py is not installed. Realtime sync will FAIL across multiple workers!",
            )
            return None
        redis_url = os.getenv("LOOP_REDIS_URL", "").strip()
        if not redis_url:
            self._warn_redis_unavailable(
                "[WARN] Redis URL not found. Realtime sync will FAIL across multiple workers!",
            )
            return None
        if self._redis_client is None:
            self._redis_client = Redis.from_url(
                redis_url,
                decode_responses=False,
                socket_connect_timeout=0.5,
                health_check_interval=30,
            )
        return self._redis_client

    @staticmethod
    def _normalize_user_id(user_id: str | int) -> str:
        return str(user_id).strip()

    def _channel_for_user(self, user_id: str | int) -> str:
        normalized_user_id = self._normalize_user_id(user_id)
        if not normalized_user_id:
            raise ValueError("Cannot publish social realtime event without user id.")
        return f"{SOCIAL_EVENTS_CHANNEL_PREFIX}{normalized_user_id}"

    def _user_id_from_channel(self, raw_channel: object) -> str:
        channel_name = (
            raw_channel.decode("utf-8")
            if isinstance(raw_channel, bytes)
            else str(raw_channel or "")
        )
        if not channel_name.startswith(SOCIAL_EVENTS_CHANNEL_PREFIX):
            raise ValueError(f"Unexpected social realtime Redis channel: {channel_name}")
        target_user_id = channel_name.split(":")[-1].strip()
        if not target_user_id:
            raise ValueError(f"Redis social realtime channel has no user id: {channel_name}")
        return target_user_id

    @staticmethod
    def _decode_redis_value(value: object) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value or "")

    @staticmethod
    def _message_key(message: str) -> str:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            return ""
        message_id = payload.get("id") or payload.get("chat_log_id")
        message_type = payload.get("type") or "message"
        return f"{message_type}:{message_id}" if message_id is not None else ""

    def _is_recent_duplicate(self, user_id: str, message_key: str) -> bool:
        now = time.monotonic()
        recent_keys = self._recent_event_keys.get(user_id)
        if not recent_keys:
            return False
        expired_keys = [
            key
            for key, seen_at in recent_keys.items()
            if now - seen_at > RECENT_EVENT_DEDUPE_SECONDS
        ]
        for key in expired_keys:
            recent_keys.pop(key, None)
        return message_key in recent_keys

    def _remember_event_key(self, user_id: str, message_key: str) -> None:
        recent_keys = self._recent_event_keys.setdefault(user_id, {})
        recent_keys[message_key] = time.monotonic()

    def _warn_redis_unavailable(self, message: str) -> None:
        if self._redis_warning_logged:
            return
        self._redis_warning_logged = True
        logger.warning(message)


social_realtime_hub = SocialRealtimeHub()
