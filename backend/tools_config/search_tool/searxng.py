"""SearXNG web search client with automatic domestic/overseas region detection, multi-node latency routing, and failover."""

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import html
import ipaddress
from logging import WARNING
import re
from threading import Lock
from time import monotonic, perf_counter
from typing import Literal
from urllib.parse import urlsplit

import httpx

from app.account_logging import log_event, logger
from app.chat.tool_context import current_tool_context
from app.system_config import SearchConfig

RegionTag = Literal['cn', 'global']

GLOBAL_PROBE_URL = 'https://www.google.com/generate_204'
HOST_REGION_TTL_SECONDS = 1800.0
NODE_HEALTH_TTL_SECONDS = 1800.0
NODE_COOLDOWN_SECONDS = 60.0
PROBE_TIMEOUT_SECONDS = 1.5
REMOTE_CAPABILITY_TIMEOUT_SECONDS = 2.2
MAX_QUERY_CHARS = 200
MAX_SNIPPET_CHARS = 220
MAX_OUTPUT_CHARS = 2200
MAX_CACHE_ENTRIES = 128
VALID_TIME_RANGES = frozenset(('', 'day', 'week', 'month', 'year'))

_CJK_RE = re.compile(r'[\u4e00-\u9fff]')
_INLINE_TAG_RE = re.compile(r'</?(?:b|strong|i|em|mark|span|u|font)\b[^>]*>', re.IGNORECASE)
_HTML_TAG_RE = re.compile(r'<[^>]+>')
_WS_RE = re.compile(r'\s+')


@dataclass
class _NodeStatus:
    region: RegionTag | None = None
    region_checked_at: float = 0.0
    latency_ms: float | None = None
    healthy: bool = True
    health_checked_at: float = 0.0
    cooldown_until: float = 0.0


def _clean_text(raw: str, *, limit: int) -> str:
    if not raw:
        return ''
    unhighlighted = _INLINE_TAG_RE.sub('', raw)
    stripped = _HTML_TAG_RE.sub(' ', unhighlighted)
    unescaped = html.unescape(stripped)
    compact = _WS_RE.sub(' ', unescaped).strip()
    if len(compact) <= limit:
        return compact
    return compact[:limit].rstrip() + '…'


def _is_local_or_private_host(hostname: str | None) -> bool:
    if not hostname:
        return True
    lower = hostname.strip('[]').lower()
    if lower in ('localhost', 'host.docker.internal') or lower.endswith('.local'):
        return True
    try:
        addr = ipaddress.ip_address(lower)
    except ValueError:
        return False
    return (
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_reserved
    )


def _normalize_result_url(url: str) -> str:
    cleaned = url.strip()
    if not cleaned:
        return ''
    try:
        parts = urlsplit(cleaned)
    except ValueError:
        return cleaned
    if parts.scheme not in ('http', 'https') or not parts.netloc:
        return ''
    return f'{parts.scheme}://{parts.netloc.lower()}{parts.path.rstrip("/")}' + (f'?{parts.query}' if parts.query else '')


class SearxngSearchRouter:
    """自动识别国内/海外网络与节点属性，按区域亲和性与实测延迟择优路由 SearXNG 请求。"""

    def __init__(self) -> None:
        self._lock = Lock()
        self._host_region: RegionTag | None = None
        self._host_region_at: float = 0.0
        self._nodes: dict[str, _NodeStatus] = {}
        self._cache: OrderedDict[tuple[str, str, str], tuple[float, str]] = OrderedDict()
        self._default_config: SearchConfig | None = None

    def _active_config(self) -> SearchConfig:
        ctx = current_tool_context()
        if ctx is not None and isinstance(ctx.search_config, SearchConfig):
            return ctx.search_config
        with self._lock:
            if self._default_config is None:
                self._default_config = SearchConfig()
            return self._default_config

    def reset_state(self) -> None:
        with self._lock:
            self._host_region = None
            self._host_region_at = 0.0
            self._nodes.clear()
            self._cache.clear()

    def detect_host_region(self) -> RegionTag:
        now = monotonic()
        with self._lock:
            if self._host_region is not None and (now - self._host_region_at) < HOST_REGION_TTL_SECONDS:
                return self._host_region

        detected: RegionTag = 'cn'
        try:
            with httpx.Client(timeout=PROBE_TIMEOUT_SECONDS, follow_redirects=False) as client:
                resp = client.head(GLOBAL_PROBE_URL)
                if 200 <= resp.status_code < 400:
                    detected = 'global'
        except Exception:
            detected = 'cn'

        now = monotonic()
        with self._lock:
            self._host_region = detected
            self._host_region_at = now
        return detected

    def _probe_remote_node_region(self, base_url: str) -> tuple[RegionTag, float | None, bool]:
        """对未显式标注区域的远程 SearXNG 节点做一次性 Google 引擎试探，判断该节点自身是否处于海外网络。"""
        start = perf_counter()
        try:
            with httpx.Client(timeout=REMOTE_CAPABILITY_TIMEOUT_SECONDS, follow_redirects=False) as client:
                resp = client.get(
                    f'{base_url}/search',
                    params={'q': 'wikipedia', 'format': 'json', 'engines': 'google'},
                    headers={'Accept': 'application/json'},
                )
            elapsed_ms = (perf_counter() - start) * 1000.0
            if resp.status_code != 200:
                return self.detect_host_region(), elapsed_ms, False
            payload = resp.json()
            if not isinstance(payload, dict):
                return self.detect_host_region(), elapsed_ms, False
            unresponsive = payload.get('unresponsive_engines') or []
            failed_names = {
                str(entry[0]).lower()
                for entry in unresponsive
                if isinstance(entry, (list, tuple)) and len(entry) >= 1
            }
            results = payload.get('results') or []
            if 'google' not in failed_names and isinstance(results, list) and len(results) > 0:
                return 'global', elapsed_ms, True
            return 'cn', elapsed_ms, True
        except Exception:
            return self.detect_host_region(), None, False

    def classify_node_region(self, base_url: str, config: SearchConfig, explicit_role: RegionTag | None = None) -> RegionTag:
        if config.region in ('cn', 'global'):
            return config.region
        if explicit_role is not None:
            return explicit_role

        hostname = urlsplit(base_url).hostname
        if _is_local_or_private_host(hostname):
            return self.detect_host_region()
        if hostname and (hostname.lower().endswith('.cn') or '.cn.' in hostname.lower()):
            return 'cn'

        now = monotonic()
        with self._lock:
            status = self._nodes.get(base_url)
            if status is not None and status.region is not None and (now - status.region_checked_at) < NODE_HEALTH_TTL_SECONDS:
                return status.region

        region, elapsed_ms, ok = self._probe_remote_node_region(base_url)
        now = monotonic()
        with self._lock:
            status = self._nodes.setdefault(base_url, _NodeStatus())
            status.region = region
            status.region_checked_at = now
            if elapsed_ms is not None:
                status.latency_ms = elapsed_ms
                status.healthy = ok
                status.health_checked_at = now
            if not ok:
                status.cooldown_until = now + NODE_COOLDOWN_SECONDS
        return region

    def _probe_node_latency(self, base_url: str) -> tuple[bool, float]:
        start = perf_counter()
        try:
            with httpx.Client(timeout=PROBE_TIMEOUT_SECONDS, follow_redirects=False) as client:
                resp = client.get(f'{base_url}/healthz')
                if resp.status_code == 404:
                    resp = client.get(f'{base_url}/config')
            elapsed_ms = (perf_counter() - start) * 1000.0
            ok = 200 <= resp.status_code < 400
            return ok, elapsed_ms
        except Exception:
            return False, 99999.0

    def _ensure_multi_node_health(self, urls: list[str]) -> None:
        now = monotonic()
        to_probe: list[str] = []
        with self._lock:
            for url in urls:
                st = self._nodes.get(url)
                if st is None or (now - st.health_checked_at) >= NODE_HEALTH_TTL_SECONDS:
                    to_probe.append(url)
        if not to_probe:
            return

        def check_one(target: str) -> tuple[str, bool, float]:
            ok, ms = self._probe_node_latency(target)
            return target, ok, ms

        with ThreadPoolExecutor(max_workers=min(4, len(to_probe))) as pool:
            outcomes = list(pool.map(check_one, to_probe))

        checked_at = monotonic()
        with self._lock:
            for target, ok, ms in outcomes:
                st = self._nodes.setdefault(target, _NodeStatus())
                st.healthy = ok
                st.latency_ms = ms
                st.health_checked_at = checked_at
                if not ok:
                    st.cooldown_until = checked_at + NODE_COOLDOWN_SECONDS

    def rank_candidates(self, config: SearchConfig) -> list[tuple[str, RegionTag]]:
        """返回按 (可用状态, 区域亲和度, 实测延迟) 排序的 (base_url, node_region) 候选列表。"""
        role_by_url: OrderedDict[str, RegionTag | None] = OrderedDict()
        for url in config.cn_urls:
            role_by_url.setdefault(url, 'cn')
        for url in config.global_urls:
            role_by_url.setdefault(url, 'global')
        for url in config.urls:
            role_by_url.setdefault(url, None)

        all_urls = list(role_by_url.keys())
        if len(all_urls) > 1:
            self._ensure_multi_node_health(all_urls)

        preferred_region: RegionTag = (
            config.region if config.region in ('cn', 'global') else self.detect_host_region()
        )
        resolved: list[tuple[str, RegionTag]] = [
            (url, self.classify_node_region(url, config, explicit_role=role))
            for url, role in role_by_url.items()
        ]

        if len(resolved) <= 1:
            return resolved

        now = monotonic()
        with self._lock:
            def sort_key(item: tuple[str, RegionTag]) -> tuple[int, int, float, int]:
                url, node_region = item
                st = self._nodes.get(url)
                in_cooldown = 1 if (st is not None and (st.cooldown_until > now or not st.healthy)) else 0
                region_mismatch = 0 if node_region == preferred_region else 1
                latency = st.latency_ms if (st is not None and st.latency_ms is not None) else 5000.0
                original_idx = all_urls.index(url)
                return (in_cooldown, region_mismatch, latency, original_idx)

            return sorted(resolved, key=sort_key)

    def _mark_success(self, base_url: str, elapsed_ms: float) -> None:
        now = monotonic()
        with self._lock:
            st = self._nodes.setdefault(base_url, _NodeStatus())
            st.healthy = True
            st.latency_ms = elapsed_ms
            st.health_checked_at = now
            st.cooldown_until = 0.0

    def _mark_failure(self, base_url: str) -> None:
        now = monotonic()
        with self._lock:
            st = self._nodes.setdefault(base_url, _NodeStatus())
            st.healthy = False
            st.cooldown_until = now + NODE_COOLDOWN_SECONDS

    def _get_cached(self, key: tuple[str, str, str], ttl: int) -> str | None:
        if ttl <= 0:
            return None
        now = monotonic()
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at <= now:
                self._cache.pop(key, None)
                return None
            self._cache.move_to_end(key)
            return value

    def _put_cached(self, key: tuple[str, str, str], value: str, ttl: int) -> None:
        if ttl <= 0:
            return
        now = monotonic()
        with self._lock:
            self._cache[key] = (now + ttl, value)
            self._cache.move_to_end(key)
            while len(self._cache) > MAX_CACHE_ENTRIES:
                self._cache.popitem(last=False)

    def search(self, query: str, time_range: str = '') -> str:
        config = self._active_config()
        if not config.enabled:
            return '[工具提示：联网搜索当前未开启，请凭已有知识自然接话或说明暂时未开启联网，不要暴露配置字段名]'

        cleaned_query = _WS_RE.sub(' ', (query or '')).strip()
        if not cleaned_query:
            return '请提供需要搜索的关键词。'
        if len(cleaned_query) > MAX_QUERY_CHARS:
            cleaned_query = cleaned_query[:MAX_QUERY_CHARS]

        norm_range = (time_range or '').strip().lower()
        if norm_range not in VALID_TIME_RANGES:
            norm_range = ''

        ctx = current_tool_context()
        if ctx is not None:
            if ctx.web_searches >= ctx.max_web_searches:
                return '[工具提示：本轮联网搜索次数已够用，请直接结合已拿到的信息自然回复对方]'
            ctx.web_searches += 1

        candidates = self.rank_candidates(config)
        if not candidates:
            return '[工具提示：未配置可用的搜索节点，请自然接话]'

        cache_region = candidates[0][1]
        cache_key = (cleaned_query.lower(), norm_range, cache_region)
        cached = self._get_cached(cache_key, config.cache_ttl_seconds)
        if cached is not None:
            logger.info('联网搜索命中内存缓存 region=%s', cache_region)
            return cached

        language = 'zh-CN' if _CJK_RE.search(cleaned_query) else 'auto'
        last_reason = 'unreachable'

        for base_url, node_region in candidates:
            engines = config.cn_engines if node_region == 'cn' else config.global_engines
            params: dict[str, str] = {
                'q': cleaned_query,
                'format': 'json',
                'engines': engines,
                'language': language,
            }
            if norm_range:
                params['time_range'] = norm_range

            start = perf_counter()
            try:
                with httpx.Client(
                    timeout=float(config.timeout_seconds),
                    follow_redirects=False,
                    headers={'Accept': 'application/json'},
                ) as client:
                    resp = client.get(f'{base_url}/search', params=params)
                elapsed_ms = (perf_counter() - start) * 1000.0
            except Exception as error:
                self._mark_failure(base_url)
                last_reason = type(error).__name__
                log_event('searxng_node_failed', level=WARNING, region=node_region, reason=last_reason)
                continue

            if resp.status_code == 403:
                self._mark_failure(base_url)
                last_reason = 'json_format_disabled_403'
                log_event('searxng_node_failed', level=WARNING, region=node_region, reason=last_reason)
                continue
            if resp.status_code != 200:
                self._mark_failure(base_url)
                last_reason = f'status_{resp.status_code}'
                log_event('searxng_node_failed', level=WARNING, region=node_region, reason=last_reason)
                continue

            try:
                payload = resp.json()
            except ValueError:
                self._mark_failure(base_url)
                last_reason = 'invalid_json'
                continue

            if not isinstance(payload, dict):
                self._mark_failure(base_url)
                last_reason = 'invalid_payload'
                continue

            raw_results = payload.get('results')
            unresponsive = payload.get('unresponsive_engines') or []
            if not isinstance(raw_results, list):
                self._mark_failure(base_url)
                last_reason = 'missing_results'
                continue

            # 若当前节点返回 0 条且存在上游引擎集体超时报错，若有备用节点则继续尝试下一台。
            if not raw_results and unresponsive and len(candidates) > 1 and base_url != candidates[-1][0]:
                last_reason = 'upstream_engines_unresponsive'
                continue

            self._mark_success(base_url, elapsed_ms)
            formatted = _format_search_results(cleaned_query, raw_results, max_results=config.max_results)
            logger.info('联网搜索完成 region=%s results=%d duration_ms=%.1f', node_region, len(raw_results), elapsed_ms)
            if raw_results:
                self._put_cached(cache_key, formatted, config.cache_ttl_seconds)
            return formatted

        log_event('tool_failure', level=WARNING, tool='web_search', reason=last_reason)
        return '[工具提示：联网搜索服务暂不可用或连接超时，请自然告诉对方这会儿网有点卡没搜出来，不要暴露内部报错或节点配置]'


def _format_search_results(query: str, raw_results: list, *, max_results: int) -> str:
    seen_urls: set[str] = set()
    items: list[str] = []

    for entry in raw_results:
        if not isinstance(entry, dict):
            continue
        raw_url = str(entry.get('url') or '').strip()
        norm_url = _normalize_result_url(raw_url)
        if not norm_url or norm_url in seen_urls:
            continue
        seen_urls.add(norm_url)

        title = _clean_text(str(entry.get('title') or ''), limit=90) or '无标题网页'
        snippet = _clean_text(str(entry.get('content') or ''), limit=MAX_SNIPPET_CHARS)
        pub_date = _clean_text(str(entry.get('publishedDate') or entry.get('pubdate') or ''), limit=32)

        header = f'{len(items) + 1}. {title}' + (f'（{pub_date}）' if pub_date else '')
        lines = [header]
        if snippet:
            lines.append(f'   摘要：{snippet}')
        lines.append(f'   链接：{raw_url}')
        items.append('\n'.join(lines))

        if len(items) >= max_results:
            break

    if not items:
        return f'未搜索到与「{query}」直接相关的网页结果，可换更简短的关键词再试，或自然告知对方没查到确切消息。'

    body = f'「{query}」的联网搜索结果：\n\n' + '\n\n'.join(items)
    if len(body) > MAX_OUTPUT_CHARS:
        body = body[:MAX_OUTPUT_CHARS].rstrip() + '…'
    return body


searxng_router = SearxngSearchRouter()


def web_search(query: str, time_range: str = '') -> str:
    logger.info('调用 SearXNG 联网搜索工具...')
    return searxng_router.search(query, time_range=time_range)
