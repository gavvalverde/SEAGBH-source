"""Bounded, replaceable HTTPS transport. No redirects, logs or automatic retry."""
from dataclasses import dataclass, field
import http.client
import json
import socket
import time


class Problem(Exception):
    """Only application-owned error codes; never retain response/header content."""
    def __init__(self, code, status=None):
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class HttpReply:
    status: int
    body: bytes = field(repr=False)


class HTTPS:
    def __init__(self, *, connect_timeout=5, read_timeout=30, total_timeout=60,
                 max_response=2 * 1024 * 1024, factory=http.client.HTTPSConnection,
                 clock=time.monotonic):
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.total_timeout = total_timeout
        self.max_response = max_response
        self.factory = factory
        self.clock = clock

    def send(self, host, method, path, headers, body=None):
        if host not in {"api.github.com", "openrouter.ai"} or not path.startswith("/"):
            raise Problem("invalid_endpoint")
        connection = response = None
        start = self.clock()
        try:
            connection = self.factory(host, timeout=self.connect_timeout)
            connection.connect()
            # HTTPConnection may detach its socket in getresponse() for Connection: close.
            read_socket = connection.sock
            read_socket.settimeout(self.read_timeout)
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            size = response.getheader("Content-Length")
            if size is not None:
                try:
                    if int(size) < 0 or int(size) > self.max_response:
                        raise Problem("response_too_large")
                except ValueError:
                    raise Problem("unexpected_response") from None
            chunks, received = [], 0
            while not response.isclosed():
                remaining = self.total_timeout - (self.clock() - start)
                if remaining <= 0:
                    raise Problem("network_timeout")
                # Do not touch the socket after the response has released its last file reference.
                read_socket.settimeout(min(self.read_timeout, remaining))
                chunk = response.read1(min(65536, self.max_response - received + 1))
                if not chunk:
                    break
                received += len(chunk)
                if received > self.max_response:
                    raise Problem("response_too_large")
                chunks.append(chunk)
            if size is not None and received != int(size):
                raise Problem("incomplete_response")
            return HttpReply(response.status, b"".join(chunks))
        except (TimeoutError, socket.timeout):
            raise Problem("network_timeout") from None
        except (OSError, http.client.HTTPException):
            raise Problem("network_error") from None
        finally:
            try:
                if response is not None:
                    response.close()
            finally:
                if connection is not None:
                    connection.close()


def decode(reply):
    try:
        return json.loads(reply.body.decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError):
        raise Problem("unexpected_response") from None


class OpenRouter:
    MAX_ATTEMPTS = 2
    MAX_RESPONSE = 1024 * 1024

    def __init__(self, key, *, http=None, sleep=time.sleep):
        if not isinstance(key, str) or not key or "\n" in key or "\r" in key:
            raise Problem("authentication_error")
        self._key = key
        self.http = http or HTTPS(max_response=self.MAX_RESPONSE)
        self.sleep = sleep

    def complete(self, payload):
        body = json.dumps(payload, ensure_ascii=True, allow_nan=False).encode("utf-8")
        if len(body) > 262144:
            raise Problem("request_too_large")
        for attempt in range(self.MAX_ATTEMPTS):
            reply = self.http.send(
                "openrouter.ai", "POST", "/api/v1/chat/completions",
                {"Authorization": "Bearer " + self._key, "Content-Type": "application/json"}, body,
            )
            if len(reply.body) > self.MAX_RESPONSE:
                raise Problem("response_too_large")
            if reply.status == 429 or 500 <= reply.status <= 599:
                if attempt + 1 < self.MAX_ATTEMPTS:
                    self.sleep(2 ** attempt)
                    continue
            if reply.status in (401, 403):
                raise Problem("authentication_error", reply.status)
            if reply.status != 200:
                raise Problem("http_error", reply.status)
            envelope = decode(reply)
            if not isinstance(envelope, dict) or "error" in envelope:
                raise Problem("external_api_error")
            choices = envelope.get("choices")
            if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
                raise Problem("unexpected_response")
            choice = choices[0]
            if choice.get("finish_reason") != "stop":
                raise Problem("incomplete_response")
            message = choice.get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant" or message.get("tool_calls"):
                raise Problem("unexpected_response")
            content = message.get("content")
            if content is not None and not isinstance(content, str):
                raise Problem("unexpected_response")
            return content
        raise Problem("http_error")
