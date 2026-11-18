"""Connect user-configured HTTPS endpoints without routing into private networks."""

import asyncio
import ipaddress
import socket

import httpx


class PublicEndpointError(ValueError):
    pass


_GLOBAL_UNICAST_V6 = ipaddress.IPv6Network('2000::/3')


def _public_address(value: str):
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address):
        if address.scope_id or address.sixtofour is not None or address.teredo is not None:
            raise PublicEndpointError('个人接口地址须指向公网')
        if address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        elif address not in _GLOBAL_UNICAST_V6:
            raise PublicEndpointError('个人接口地址须指向公网')
    if not address.is_global or address.is_multicast or address.is_reserved:
        raise PublicEndpointError('个人接口地址须指向公网')
    return address


class PublicEndpoint:
    def __init__(self, base_url: str):
        self.url = httpx.URL(base_url)
        if self.url.scheme != 'https' or not self.url.host:
            raise PublicEndpointError('个人自定义接口须使用公网 HTTPS 地址')
        host = self.url.host.rstrip('.').lower()
        if host == 'localhost' or host.endswith('.localhost'):
            raise PublicEndpointError('个人接口地址须指向公网')
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            _public_address(host)

    def pin(self, request: httpx.Request) -> httpx.Request:
        url = request.url
        if (url.scheme, url.raw_host, url.port) != (self.url.scheme, self.url.raw_host, self.url.port):
            raise PublicEndpointError('个人接口请求须发送到所配置的地址')
        try:
            answers = socket.getaddrinfo(url.host, url.port or 443, type=socket.SOCK_STREAM)
        except socket.gaierror:
            raise PublicEndpointError('无法解析个人接口域名') from None
        if not answers:
            raise PublicEndpointError('无法解析个人接口域名')
        addresses = [_public_address(answer[4][0]) for answer in answers]
        address = next((item for item in addresses if item.version == 4), addresses[0])
        # 按校验后的 IP 建连，HTTP Host 和 TLS 证书校验仍使用原域名。
        headers = httpx.Headers(request.headers)
        headers['Host'] = url.netloc.decode('ascii')
        return httpx.Request(request.method, url.copy_with(host=str(address)), headers=headers, stream=request.stream,
                             extensions={**request.extensions, 'sni_hostname': url.raw_host.decode('ascii')})


class _PublicTransport(httpx.HTTPTransport):
    def __init__(self, endpoint: PublicEndpoint):
        super().__init__(trust_env=False)
        self.endpoint = endpoint

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return super().handle_request(self.endpoint.pin(request))


class _AsyncPublicTransport(httpx.AsyncHTTPTransport):
    def __init__(self, endpoint: PublicEndpoint):
        super().__init__(trust_env=False)
        self.endpoint = endpoint

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await super().handle_async_request(await asyncio.to_thread(self.endpoint.pin, request))


def public_http_clients(base_url: str, timeout: float) -> dict:
    endpoint = PublicEndpoint(base_url)
    options = {'timeout': timeout, 'trust_env': False, 'follow_redirects': False}
    return {
        'http_client': httpx.Client(transport=_PublicTransport(endpoint), **options),
        'http_async_client': httpx.AsyncClient(transport=_AsyncPublicTransport(endpoint), **options),
        'openai_proxy': '',
    }
