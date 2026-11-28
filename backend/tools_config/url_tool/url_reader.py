"""Safely fetch and extract readable text from a public HTTP(S) web page with SSRF pinning on every hop."""

import json
import ipaddress
import re
import socket
from html.parser import HTMLParser
from logging import WARNING
from time import monotonic

import httpx

from app.account_logging import log_event, logger
from app.chat.progress import check_cancelled
from app.llm.public_endpoint import PublicEndpointError, _public_address

MAX_REDIRECTS = 3
MAX_DOWNLOAD_BYTES = 262_144
MAX_CONTENT_CHARS = 2500
MAX_FETCH_SECONDS = 6.5
USER_AGENT = 'Mozilla/5.0 (compatible; XiaowanLinkReader/1.0)'

ALLOWED_CONTENT_TYPES = frozenset((
    'text/html',
    'text/plain',
    'application/xhtml+xml',
    'text/markdown',
    'text/xml',
    'application/xml',
))
IGNORED_TAGS = frozenset((
    'script', 'style', 'noscript', 'svg', 'canvas', 'iframe', 'template', 'nav', 'footer', 'aside',
))
BLOCK_TAGS = frozenset((
    'p', 'div', 'article', 'section', 'main', 'li', 'ul', 'ol', 'blockquote', 'pre',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'br', 'tr', 'hr',
))
_CHARSET_RE = re.compile(rb'<meta[^>]+charset=["\']?\s*([a-zA-Z0-9._-]+)', re.IGNORECASE)
_EMBEDDED_HTML_PARAGRAPHS_RE = re.compile(
    r"""['"]((?:<p\b[^>]*>[\s\S]*?</p>\s*){2,})['"]""",
    re.IGNORECASE,
)


class _ArticleHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._in_title = False
        self._current_script_type: str | None = None
        self._script_buf: list[str] = []
        self.title_parts: list[str] = []
        self.description: str = ''
        self.chunks: list[str] = []
        self.paragraph_chars: int = 0
        self._in_p = False
        self.scripts: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lower = tag.lower()
        if lower == 'script':
            if self._ignored_depth == 0:
                attr_map = {(k or '').lower(): (v or '').strip().lower() for k, v in attrs}
                self._current_script_type = attr_map.get('type', '')
                self._script_buf = []
            self._ignored_depth += 1
            return
        if lower in IGNORED_TAGS:
            self._ignored_depth += 1
            return
        if lower == 'title':
            self._in_title = True
            return
        if lower == 'meta' and not self.description:
            attr_map = {(k or '').lower(): (v or '').strip() for k, v in attrs}
            name = attr_map.get('name', '').lower() or attr_map.get('property', '').lower()
            if name in ('description', 'og:description') and attr_map.get('content'):
                self.description = attr_map['content']
            return
        if self._ignored_depth == 0:
            if lower == 'p':
                self._in_p = True
            if lower in BLOCK_TAGS:
                self.chunks.append('\n')

    def handle_endtag(self, tag: str) -> None:
        lower = tag.lower()
        if lower == 'script' and self._ignored_depth > 0:
            self._ignored_depth -= 1
            if self._ignored_depth == 0 and self._current_script_type is not None:
                raw_script = ''.join(self._script_buf).strip()
                if raw_script:
                    self.scripts.append((self._current_script_type, raw_script))
                self._current_script_type = None
                self._script_buf = []
            return
        if lower in IGNORED_TAGS and self._ignored_depth > 0:
            self._ignored_depth -= 1
            return
        if lower == 'title':
            self._in_title = False
            return
        if self._ignored_depth == 0:
            if lower == 'p':
                self._in_p = False
            if lower in BLOCK_TAGS:
                self.chunks.append('\n')

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        elif self._current_script_type is not None and self._ignored_depth == 1:
            self._script_buf.append(data)
        elif self._ignored_depth == 0:
            self.chunks.append(data)
            if self._in_p:
                self.paragraph_chars += len(data.strip())


def _validate_and_resolve_url(raw_url: str) -> tuple[httpx.URL, str]:
    try:
        url = httpx.URL(raw_url)
    except httpx.InvalidURL:
        raise PublicEndpointError('链接格式无效') from None

    if url.scheme not in ('http', 'https') or not url.host:
        raise PublicEndpointError('仅支持公网 HTTP 或 HTTPS 网页链接')
    if url.username or url.password:
        raise PublicEndpointError('链接不能包含账号凭据')
    host = url.host.rstrip('.').lower()
    if host == 'localhost' or host.endswith('.localhost'):
        raise PublicEndpointError('仅支持访问公网地址')
    try:
        port = url.port or (443 if url.scheme == 'https' else 80)
    except ValueError:
        raise PublicEndpointError('链接端口无效') from None
    if not (1 <= port <= 65535):
        raise PublicEndpointError('链接端口无效')

    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return url, str(_public_address(host))

    try:
        answers = socket.getaddrinfo(url.host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise PublicEndpointError('无法解析该网页域名') from None
    if not answers:
        raise PublicEndpointError('无法解析该网页域名')
    addresses = [_public_address(answer[4][0]) for answer in answers]
    chosen = next((item for item in addresses if item.version == 4), addresses[0])
    return url, str(chosen)


class _PinnedHopTransport(httpx.HTTPTransport):
    """单跳公网 IP 绑定传输层，保留原域名 Host 与 HTTPS SNI。"""

    def __init__(self, expected_url: httpx.URL, pinned_ip: str):
        super().__init__(trust_env=False)
        self._url = expected_url
        self._ip = pinned_ip

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        headers = httpx.Headers(request.headers)
        headers['Host'] = url.netloc.decode('ascii')
        extensions = dict(request.extensions)
        if url.scheme == 'https':
            extensions['sni_hostname'] = url.raw_host.decode('ascii')
        pinned = httpx.Request(
            request.method,
            url.copy_with(host=self._ip),
            headers=headers,
            stream=request.stream,
            extensions=extensions,
        )
        return super().handle_request(pinned)


def _decode_bytes(raw: bytes, declared_encoding: str | None) -> str:
    candidates: list[str] = []
    if declared_encoding:
        candidates.append(declared_encoding)
    meta_match = _CHARSET_RE.search(raw[:4096])
    if meta_match:
        try:
            candidates.append(meta_match.group(1).decode('ascii', errors='ignore'))
        except Exception:
            pass
    candidates.extend(('utf-8', 'gb18030'))
    for enc in candidates:
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode('utf-8', errors='replace')


def _clean_text(text: str) -> str:
    lines = []
    for raw_line in text.splitlines():
        line = re.sub(r'[ \t\r\f\v]+', ' ', raw_line).strip()
        if line:
            lines.append(line)
    return '\n'.join(lines)


def _extract_script_article(scripts: list[tuple[str, str]]) -> str:
    """当静态正文由脚本变量或 JSON-LD 注入（如央视网 contentdate、新闻站点 articleBody）时提取正文段落。"""
    best = ''
    for script_type, content in scripts:
        if script_type == 'application/ld+json':
            try:
                data = json.loads(content)
            except ValueError:
                data = None
            items = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
            for obj in items:
                if isinstance(obj, dict):
                    article_body = obj.get('articleBody')
                    if isinstance(article_body, str) and len(article_body.strip()) > len(best):
                        best = _clean_text(article_body)

        for match in _EMBEDDED_HTML_PARAGRAPHS_RE.finditer(content):
            fragment = (
                match.group(1)
                .replace(r'\/', '/')
                .replace(r'\"', '"')
                .replace(r"\'", "'")
                .replace(r'\n', '\n')
                .replace(r'\t', ' ')
            )
            frag_parser = _ArticleHTMLParser()
            frag_parser.feed(fragment)
            frag_parser.close()
            extracted = _clean_text(''.join(frag_parser.chunks))
            if len(extracted) > len(best):
                best = extracted
    return best


def _extract_readable(raw_bytes: bytes, content_type: str, encoding: str | None) -> tuple[str, str, str]:
    decoded = _decode_bytes(raw_bytes, encoding)
    if content_type == 'text/plain' or content_type == 'text/markdown':
        return '', '', _clean_text(decoded)
    parser = _ArticleHTMLParser()
    parser.feed(decoded)
    parser.close()
    title = re.sub(r'\s+', ' ', ''.join(parser.title_parts)).strip()
    description = re.sub(r'\s+', ' ', parser.description).strip()
    body = _clean_text(''.join(parser.chunks))
    # 若页面可见 <p> 正文极少（常见于把文章存在 JS 变量或 JSON-LD 的门户页），优先采用提取到的脚本正文。
    if parser.paragraph_chars < 120 and parser.scripts:
        embedded = _extract_script_article(parser.scripts)
        if len(embedded) > parser.paragraph_chars:
            body = embedded
    return title, description, body


def _fetch_single_hop(url: httpx.URL, pinned_ip: str, timeout: httpx.Timeout):
    transport = _PinnedHopTransport(url, pinned_ip)
    return httpx.Client(
        transport=transport,
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
        headers={
            'User-Agent': USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1',
            'Accept-Encoding': 'gzip, deflate',
        },
    )


def read_url(url: str) -> str:
    logger.info('调用网页链接阅读工具...')
    cleaned_url = url.strip()
    if not cleaned_url or len(cleaned_url) > 2000:
        return '链接无效或过长，请检查链接地址。'

    deadline = monotonic() + MAX_FETCH_SECONDS
    current_raw = cleaned_url

    for hop in range(MAX_REDIRECTS + 1):
        check_cancelled()
        remaining = deadline - monotonic()
        if remaining <= 0.2:
            log_event('tool_failure', level=WARNING, tool='read_url', reason='deadline_exceeded')
            return '[工具提示：网页响应较慢，读取超时]'

        try:
            parsed_url, pinned_ip = _validate_and_resolve_url(current_raw)
        except PublicEndpointError as error:
            log_event('tool_failure', level=WARNING, tool='read_url', reason='blocked_endpoint')
            return f'无法读取该链接：{error}'

        timeout = httpx.Timeout(
            connect=min(3.0, remaining),
            read=min(4.5, remaining),
            write=min(3.0, remaining),
            pool=min(3.0, remaining),
        )
        try:
            with _fetch_single_hop(parsed_url, pinned_ip, timeout) as client:
                with client.stream('GET', parsed_url) as response:
                    status = response.status_code
                    if status in (301, 302, 303, 307, 308):
                        location = (response.headers.get('location') or '').strip()
                        if not location:
                            return '[工具提示：网页重定向缺少目标地址，无法读取]'
                        if hop >= MAX_REDIRECTS:
                            return '[工具提示：网页跳转次数过多，无法读取]'
                        current_raw = str(parsed_url.join(location))
                        continue

                    if status >= 400:
                        log_event('tool_failure', level=WARNING, tool='read_url', http_status=status)
                        return f'[工具提示：该网页暂时无法打开（HTTP {status}）]'

                    raw_ct = (response.headers.get('content-type') or '').split(';', 1)[0].strip().lower()
                    if raw_ct and raw_ct not in ALLOWED_CONTENT_TYPES:
                        return f'[工具提示：该链接是 {raw_ct} 类型文件或媒体，暂时只能读取普通网页正文]'

                    chunks: list[bytes] = []
                    downloaded = 0
                    truncated_bytes = False
                    for part in response.iter_bytes(chunk_size=8192):
                        check_cancelled()
                        if monotonic() > deadline:
                            truncated_bytes = True
                            break
                        if not part:
                            continue
                        budget = MAX_DOWNLOAD_BYTES - downloaded
                        if len(part) > budget:
                            chunks.append(part[:budget])
                            downloaded += budget
                            truncated_bytes = True
                            break
                        chunks.append(part)
                        downloaded += len(part)
                    encoding = response.encoding
                    final_url = str(parsed_url)
        except httpx.TimeoutException:
            log_event('tool_failure', level=WARNING, tool='read_url', error='TimeoutException')
            return '[工具提示：网页加载超时，未能读取内容]'
        except httpx.HTTPError as error:
            log_event('tool_failure', level=WARNING, tool='read_url', error=type(error).__name__)
            return '[工具提示：网页连接失败，暂时无法读取内容]'

        title, description, body = _extract_readable(b''.join(chunks), raw_ct or 'text/html', encoding)
        if not title and not description and not body:
            return '[工具提示：该网页没有可直接读取的文字正文（可能需要登录或纯前端渲染）]'

        parts = [f'链接：{final_url}']
        if title:
            parts.append(f'标题：{title[:200]}')
        if description and description not in body[:400]:
            parts.append(f'简介：{description[:300]}')
        if body:
            if len(body) > MAX_CONTENT_CHARS:
                body = body[:MAX_CONTENT_CHARS] + '…（正文较长已截断）'
            elif truncated_bytes:
                body = body + '…（页面较大仅截取前段）'
            parts.append(f'正文摘录：\n{body}')
        return '\n'.join(parts)

    return '[工具提示：网页跳转过多，无法读取]'
