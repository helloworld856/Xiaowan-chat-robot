import socket
import unittest
from unittest.mock import patch

import httpx

import _imports


class PublicModelEndpointTests(unittest.TestCase):
    def test_resolves_to_a_public_ip_without_changing_the_http_host_or_tls_name(self):
        from app.llm.public_endpoint import PublicEndpoint

        endpoint = PublicEndpoint('https://models.example.test:8443/v1')
        request = httpx.Request('POST', 'https://models.example.test:8443/v1/chat/completions')
        with patch('app.llm.public_endpoint.socket.getaddrinfo', return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 8443)),
        ]):
            public = endpoint.pin(request)
        self.assertEqual(public.url.host, '93.184.216.34')
        self.assertEqual(public.headers['Host'], 'models.example.test:8443')
        self.assertEqual(public.extensions['sni_hostname'], 'models.example.test')
        self.assertEqual(request.url.host, 'models.example.test')

    def test_rejects_private_ips_in_dns_and_requests_to_a_different_origin(self):
        from app.llm.public_endpoint import PublicEndpoint, PublicEndpointError

        endpoint = PublicEndpoint('https://models.example.test/v1')
        with patch('app.llm.public_endpoint.socket.getaddrinfo', return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443)),
        ]):
            with self.assertRaises(PublicEndpointError):
                endpoint.pin(httpx.Request('POST', 'https://models.example.test/v1/chat/completions'))
        with self.assertRaises(PublicEndpointError):
            endpoint.pin(httpx.Request('POST', 'https://other.example.test/v1/chat/completions'))
        with self.assertRaises(PublicEndpointError):
            PublicEndpoint('https://192.168.0.1/v1')
