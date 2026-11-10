import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from config import ALTERNATING_MODELS_POOL

logger = logging.getLogger("groq_scheduler")

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

P0_INTERACTIVE = 0
P1_REALTIME = 1
P2_BATCH_SCAN = 2
P3_MAINTENANCE = 3


class GroqBusyError(RuntimeError):
    """Raised when the Groq scheduler queue is saturated or models are rate-limited past deadline."""
    pass


def parse_groq_duration(val: Optional[str]) -> float:
    """
    Parse Groq header duration strings such as '577ms', '2.5s', '1m26.4s', '1h2m3s' into seconds.
    """
    if not val:
        return 0.0
    val = str(val).strip()
    if val.replace(".", "", 1).isdigit():
        return float(val)
    total = 0.0
    m_hr = re.search(r"(\d+(?:\.\d+)?)h", val)
    if m_hr:
        total += float(m_hr.group(1)) * 3600.0
    m_min = re.search(r"(\d+(?:\.\d+)?)m(?!s)", val)
    if m_min:
        total += float(m_min.group(1)) * 60.0
    m_sec = re.search(r"(\d+(?:\.\d+)?)s", val)
    if m_sec:
        total += float(m_sec.group(1))
    m_ms = re.search(r"(\d+(?:\.\d+)?)ms", val)
    if m_ms:
        total += float(m_ms.group(1)) / 1000.0
    return total


@dataclass
class ModelBucket:
    model: str
    tpm_limit: int = 8000
    rpd_limit: int = 1000
    remaining_tokens: int = 8000
    remaining_requests: int = 1000
    reset_tokens_at: float = 0.0
    reset_requests_at: float = 0.0
    cooldown_until: float = 0.0
    usage_count: int = 0
    in_flight: int = 0
    in_flight_tokens: int = 0

    def update_from_headers(self, headers: Any) -> None:
        now = time.time()
        rem_tok = headers.get("x-ratelimit-remaining-tokens")
        if rem_tok is not None and str(rem_tok).isdigit():
            self.remaining_tokens = int(rem_tok)
        lim_tok = headers.get("x-ratelimit-limit-tokens")
        if lim_tok is not None and str(lim_tok).isdigit():
            self.tpm_limit = int(lim_tok)
        rst_tok = headers.get("x-ratelimit-reset-tokens")
        if rst_tok:
            self.reset_tokens_at = now + parse_groq_duration(rst_tok)

        rem_req = headers.get("x-ratelimit-remaining-requests")
        if rem_req is not None and str(rem_req).isdigit():
            self.remaining_requests = int(rem_req)
        lim_req = headers.get("x-ratelimit-limit-requests")
        if lim_req is not None and str(lim_req).isdigit():
            self.rpd_limit = int(lim_req)
        rst_req = headers.get("x-ratelimit-reset-requests")
        if rst_req:
            self.reset_requests_at = now + parse_groq_duration(rst_req)

    def effective_remaining_tokens(self, now: float) -> int:
        base = self.tpm_limit if now >= self.reset_tokens_at else self.remaining_tokens
        return max(0, base - self.in_flight_tokens)

    def is_available_for(
        self, now: float, est_tokens: int, priority: int, allow_shared: bool = False
    ) -> Tuple[bool, float]:
        """
        Check if this model bucket can immediately serve a job of `est_tokens` at `priority`.
        Fix P1-7: When `in_flight >= 1` and `allow_shared=False`, prefer another idle model first
        so concurrent workers naturally distribute across our 3 models in parallel.
        """
        if now < self.cooldown_until:
            return False, self.cooldown_until - now

        if self.in_flight >= 1 and not allow_shared:
            return False, 0.35

        # Protect last 50 daily requests for P0/P1 interactive & realtime tasks
        if priority >= P2_BATCH_SCAN and self.remaining_requests < 50 and now < self.reset_requests_at:
            return False, max(60.0, self.reset_requests_at - now)

        eff_tokens = self.effective_remaining_tokens(now)
        needed = min(est_tokens, int(self.tpm_limit * 0.85))
        if eff_tokens < needed and now < self.reset_tokens_at:
            return False, max(0.2, self.reset_tokens_at - now)

        return True, 0.0


@dataclass(order=True)
class ScheduledGroqJob:
    priority: int
    seq: int
    payload: Dict[str, Any] = field(compare=False)
    allowed_models: List[str] = field(compare=False)
    est_tokens: int = field(compare=False)
    future: asyncio.Future = field(compare=False)


class GroqScheduler:
    """
    Centralized Priority Queue & Multi-Worker Rate-Limit Scheduler for Groq API (Fix P1-7).
    Runs N=len(models) concurrent workers so independent model buckets are utilized in parallel
    without a single slow request blocking interactive P0 chats.
    """

    def __init__(self, api_key: str, models: Optional[List[str]] = None):
        self.api_key = api_key.strip()
        self.models = models or list(ALTERNATING_MODELS_POOL)
        self.buckets: Dict[str, ModelBucket] = {m: ModelBucket(model=m) for m in self.models}
        self._rr_counter: int = 0
        self._seq_counter: int = 0
        self._queue: asyncio.PriorityQueue[ScheduledGroqJob] = asyncio.PriorityQueue(maxsize=100)
        self._worker_tasks: List[asyncio.Task] = []
        self._session: Optional[aiohttp.ClientSession] = None
        self._active_user_tasks: Dict[Tuple[str, str], int] = {}
        self.consecutive_all_failed: int = 0

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=45, connect=10)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    def _ensure_workers(self) -> None:
        self._worker_tasks = [t for t in self._worker_tasks if not t.done()]
        target_workers = max(1, len(self.models))
        while len(self._worker_tasks) < target_workers:
            idx = len(self._worker_tasks)
            self._worker_tasks.append(asyncio.create_task(self._worker_loop(idx)))

    async def close(self) -> None:
        for t in self._worker_tasks:
            if not t.done():
                t.cancel()
        self._worker_tasks.clear()
        if self._session and not self._session.closed:
            await self._session.close()

    def acquire_user_slot(self, user_id: str, kind: str = "ai") -> bool:
        key = (str(user_id), kind)
        if self._active_user_tasks.get(key, 0) >= 1:
            return False
        self._active_user_tasks[key] = self._active_user_tasks.get(key, 0) + 1
        return True

    def release_user_slot(self, user_id: str, kind: str = "ai") -> None:
        key = (str(user_id), kind)
        if key in self._active_user_tasks:
            self._active_user_tasks[key] = max(0, self._active_user_tasks[key] - 1)
            if self._active_user_tasks[key] == 0:
                del self._active_user_tasks[key]

    def get_queue_status(self) -> Dict[str, Any]:
        q_size = self._queue.qsize()
        now = time.time()
        models_info = {}
        for m, b in self.buckets.items():
            short_name = m.split("/")[-1]
            models_info[short_name] = {
                "used": b.usage_count,
                "rem_tok": b.effective_remaining_tokens(now),
                "rem_req": b.remaining_requests,
                "in_flight": b.in_flight,
                "cooldown": max(0, int(b.cooldown_until - now)),
            }
        return {
            "queue_size": q_size,
            "estimated_wait_sec": max(1, (q_size * 2) // max(1, len(self.models))) if q_size else 0,
            "models": models_info,
        }

    def get_rotated_models(self) -> List[str]:
        idx = self._rr_counter % len(self.models)
        self._rr_counter += 1
        return self.models[idx:] + self.models[:idx]

    @staticmethod
    def estimate_tokens(
        messages: List[Dict[str, Any]], tools: Optional[List[Dict]], max_tokens: int
    ) -> int:
        chars = sum(len(str(m.get("content", ""))) for m in messages)
        if tools:
            chars += len(str(tools)) // 2
        return int(chars / 1.8) + min(max_tokens, 600)

    async def submit_chat(
        self,
        messages: List[Dict[str, Any]],
        priority: int = P1_REALTIME,
        tools: Optional[List[Dict]] = None,
        allowed_models: Optional[List[str]] = None,
        temperature: float = 0.1,
        max_tokens: int = 1200,
        json_mode: bool = False,
    ) -> Dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("尚未設定 GROQ_API_KEY")

        self._ensure_workers()
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._seq_counter += 1
        models_order = allowed_models or self.get_rotated_models()
        est_tok = self.estimate_tokens(messages, tools, max_tokens)

        job = ScheduledGroqJob(
            priority=priority,
            seq=self._seq_counter,
            payload={
                "messages": messages,
                "tools": tools,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "json_mode": json_mode,
            },
            allowed_models=models_order,
            est_tokens=est_tok,
            future=fut,
        )
        max_q = self._queue.maxsize or 100
        if priority >= P2_BATCH_SCAN and self._queue.qsize() >= int(max_q * 0.7):
            raise GroqBusyError("系統 AI 排程佇列負載達 70%，已暫緩背景批次任務以保留互動資源。")

        if self._queue.full():
            if priority <= P1_REALTIME and hasattr(self._queue, "_queue") and self._queue._queue:
                # Find the lowest-priority (highest priority integer, then highest seq) job in queue to evict
                worst_idx = max(
                    range(len(self._queue._queue)),
                    key=lambda idx: (self._queue._queue[idx].priority, self._queue._queue[idx].seq),
                )
                evicted = self._queue._queue[worst_idx]
                if evicted.priority > priority:
                    del self._queue._queue[worst_idx]
                    import heapq
                    heapq.heapify(self._queue._queue)
                    if not evicted.future.done():
                        evicted.future.set_exception(
                            GroqBusyError("系統 AI 排程佇列滿載，低優先級任務已被高優先級請求取代。")
                        )
                else:
                    raise GroqBusyError("系統 AI 排程佇列已滿（上限 100 任務），請稍後再試。")
            else:
                raise GroqBusyError("系統 AI 排程佇列已滿（上限 100 任務），請稍後再試。")
        await self._queue.put(job)
        return await fut

    async def _worker_loop(self, worker_id: int = 0) -> None:
        while True:
            try:
                job = await self._queue.get()
                if job.future.cancelled():
                    self._queue.task_done()
                    continue

                try:
                    result = await self._execute_job(job)
                    self.consecutive_all_failed = 0
                    if not job.future.done():
                        job.future.set_result(result)
                except Exception as e:
                    self.consecutive_all_failed += 1
                    if not job.future.done():
                        job.future.set_exception(e)
                finally:
                    self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"GroqScheduler worker#{worker_id} 迴圈異常: {e}", exc_info=True)
                await asyncio.sleep(1.0)

    async def _execute_job(self, job: ScheduledGroqJob) -> Dict[str, Any]:
        session = await self._get_session()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        p_data = job.payload
        last_err = None

        max_wait_budget = 30.0 if job.priority == P0_INTERACTIVE else 120.0
        deadline = time.time() + max_wait_budget

        for attempt in range(2):
            ready_models: List[str] = []

            while not ready_models:
                now = time.time()
                min_wait = 999.0

                # Pass 1: prefer idle models (in_flight == 0)
                for m in job.allowed_models:
                    bucket = self.buckets.get(m)
                    if not bucket:
                        ready_models.append(m)
                        continue
                    ok, wait_s = bucket.is_available_for(
                        now, job.est_tokens, job.priority, allow_shared=False
                    )
                    if ok:
                        ready_models.append(m)
                    else:
                        min_wait = min(min_wait, wait_s)

                # Pass 2: if all models have in_flight >= 1, allow sharing a model with remaining token quota
                if not ready_models:
                    for m in job.allowed_models:
                        bucket = self.buckets.get(m)
                        if bucket:
                            ok, wait_s = bucket.is_available_for(
                                now, job.est_tokens, job.priority, allow_shared=True
                            )
                            if ok:
                                ready_models.append(m)
                            else:
                                min_wait = min(min_wait, wait_s)

                if ready_models:
                    break

                remaining_budget = deadline - now
                if remaining_budget <= 0:
                    raise GroqBusyError(
                        f"所有 AI 模型目前皆處於額度冷卻或忙碌中 (已等待逾 {max_wait_budget:.0f}s)，請稍後再試。"
                    )

                step_cap = 5.0 if job.priority == P0_INTERACTIVE else 10.0
                sleep_dur = min(min_wait, remaining_budget, step_cap)
                if sleep_dur > 0.05:
                    logger.info(
                        f"[GroqScheduler] 所有模型額度恢復等待中，暫候 {sleep_dur:.1f}s (priority={job.priority})"
                    )
                    await asyncio.sleep(sleep_dur)
                else:
                    await asyncio.sleep(0.05)

            for model_name in ready_models:
                bucket = self.buckets.get(model_name)
                req_body: Dict[str, Any] = {
                    "model": model_name,
                    "messages": p_data["messages"],
                    "temperature": p_data["temperature"],
                    "max_tokens": p_data["max_tokens"],
                }
                if p_data.get("tools"):
                    req_body["tools"] = p_data["tools"]
                    req_body["tool_choice"] = "auto"
                if p_data.get("json_mode") and "gpt-oss" in model_name:
                    req_body["response_format"] = {"type": "json_object"}

                if bucket:
                    bucket.in_flight += 1
                    bucket.in_flight_tokens += job.est_tokens

                try:
                    async with session.post(GROQ_API_URL, headers=headers, json=req_body) as resp:
                        status = resp.status
                        if bucket:
                            bucket.update_from_headers(resp.headers)
                        data = await resp.json()

                        if status == 200 and "choices" in data:
                            if bucket:
                                bucket.usage_count += 1
                            data["_used_model"] = model_name
                            return data

                        if status == 429 and bucket:
                            now_429 = time.time()
                            retry_after = parse_groq_duration(resp.headers.get("retry-after")) or 6.0
                            if bucket.remaining_requests <= 0 or retry_after > 60.0:
                                req_reset_wait = max(0.0, bucket.reset_requests_at - now_429)
                                cd_sec = max(retry_after, req_reset_wait, 60.0)
                            else:
                                cd_sec = min(retry_after, 60.0)
                            bucket.cooldown_until = now_429 + cd_sec
                            logger.warning(
                                f"[GroqScheduler] {model_name} 觸發 429，冷卻 {cd_sec:.1f}s，自動切換下一個模型"
                            )
                        err_msg = data.get("error", {}).get("message", str(data))
                        last_err = f"HTTP {status} ({model_name}): {err_msg}"
                except Exception as e:
                    logger.warning(f"[GroqScheduler] {model_name} 請求失敗: {e}")
                    last_err = str(e)
                finally:
                    if bucket:
                        bucket.in_flight = max(0, bucket.in_flight - 1)
                        bucket.in_flight_tokens = max(0, bucket.in_flight_tokens - job.est_tokens)

            await asyncio.sleep(1.0)

        raise RuntimeError(f"Groq 排程呼叫失敗: {last_err}")
