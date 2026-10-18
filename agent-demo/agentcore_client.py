"""Minimal AgentCore AG-UI client. Python 3.9+, standard library only."""

import json
import os
import socket
import uuid
from dataclasses import dataclass
from http.client import HTTPException
from pathlib import Path
from typing import Dict, Iterator
from urllib import error, parse, request


class AgentCoreError(RuntimeError):
    """Configuration, transport, or Agent execution failed."""


@dataclass
class AgentReply:
    thread_id: str
    run_id: str
    messages: Dict[str, str]

    @property
    def text(self) -> str:
        return "\n".join(self.messages.values())


def load_config(env_file: Path):
    """Read two settings; environment variables take precedence over .env."""
    values = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, value = line.partition("=")
            if not sep:
                raise AgentCoreError(".env 中每项必须使用 名称=值 格式。")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[key.strip()] = value
    keys = ("AGENTCORE_ENDPOINT", "AGENTCORE_ENDPOINT_API_KEY")
    result = [os.environ.get(k, values.get(k, "")).strip() for k in keys]
    missing = [k for k, value in zip(keys, result) if not value]
    if missing:
        raise AgentCoreError("请先填写 agent-demo/.env：" + "、".join(missing))
    return tuple(result)


def agent_url(endpoint: str) -> str:
    endpoint = endpoint.strip().rstrip("/")
    try:
        parts = parse.urlsplit(endpoint)
        parts.port
    except ValueError as exc:
        raise AgentCoreError("Endpoint 地址格式或端口不正确。") from exc
    if (parts.scheme not in ("https", "http") or not parts.hostname
            or parts.username is not None or parts.password is not None
            or parts.query or parts.fragment or any(c.isspace() for c in endpoint)):
        raise AgentCoreError("Endpoint 应为 http(s) 地址，不应包含账号、查询参数或 #。")
    if not parts.path.endswith("/ag-ui/agent"):
        endpoint += "/ag-ui/agent"
    return endpoint


def read_events(response) -> Iterator[dict]:
    """Parse SSE frames, including heartbeat comments and multiline data."""
    data_lines = []
    for raw in response:
        line = raw.decode("utf-8-sig").rstrip("\r\n")
        if not line:
            if data_lines:
                try:
                    event = json.loads("\n".join(data_lines))
                except json.JSONDecodeError as exc:
                    raise AgentCoreError("SSE 事件不是有效 JSON。") from exc
                if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                    raise AgentCoreError("SSE 事件缺少有效的 type 字段。")
                yield event
                data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if field == "data":
            data_lines.append(value[1:] if value.startswith(" ") else value)
    # A frame without its final blank line is incomplete and must not count as success.


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def call_agent(endpoint: str, api_key: str, message: str, *, thread_id=None,
               run_id=None, timeout=90, system_prompt=None, on_event=None) -> AgentReply:
    """Call once. HTTP success alone does not establish Agent success.

    timeout is the socket connection/read timeout, not a whole-run time budget.
    No automatic retries: a broken connection may leave a cloud run executing.
    """
    if not api_key.strip() or "\r" in api_key or "\n" in api_key:
        raise AgentCoreError("API Key 不能为空，也不能包含换行。")
    if not message.strip() or timeout <= 0:
        raise AgentCoreError("消息不能为空，timeout 必须大于 0。")
    thread_id = thread_id or "aps-" + uuid.uuid4().hex
    run_id = run_id or "run-" + uuid.uuid4().hex
    payload = {
        "threadId": thread_id, "runId": run_id,
        "messages": [{"id": "msg-" + uuid.uuid4().hex,
                      "role": "user", "content": message}],
        "state": {}, "tools": [], "context": [], "forwardedProps": {},
    }
    if system_prompt:
        payload['messages'].insert(0, {'id': 'sys-' + uuid.uuid4().hex,
                                      'role': 'system', 'content': system_prompt})
    req = request.Request(
        agent_url(endpoint),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream",
                 "x-api-key": api_key, "X-AgentCore-Session-ID": thread_id},
        method="POST",
    )
    messages = {}
    try:
        with request.build_opener(_NoRedirect()).open(req, timeout=timeout) as response:
            if response.status != 200:
                raise AgentCoreError("非预期 HTTP 状态：" + str(response.status))
            if response.headers.get_content_type() != "text/event-stream":
                raise AgentCoreError("返回内容不是 SSE，请检查是否填成了控制台地址或模型 API 地址。")
            for event in read_events(response):
                if on_event:
                    on_event(event)
                for field, expected in (("threadId", thread_id), ("runId", run_id)):
                    if field in event and event[field] != expected:
                        raise AgentCoreError("返回事件的 " + field + " 与请求不一致。")
                kind = event["type"]
                if kind == "TEXT_MESSAGE_START":
                    if event.get("messageId"):
                        messages.setdefault(event["messageId"], "")
                elif kind == "TEXT_MESSAGE_CONTENT":
                    msg_id, delta = event.get("messageId"), event.get("delta")
                    if not isinstance(msg_id, str) or not isinstance(delta, str):
                        raise AgentCoreError("文本事件缺少有效的 messageId 或 delta。")
                    messages[msg_id] = messages.get(msg_id, "") + delta
                elif kind == "RUN_ERROR":
                    detail = str(event.get("message", "未提供错误说明")).replace(api_key, "[REDACTED]")
                    raise AgentCoreError(f"Agent 执行失败 [{event.get('code', 'UNKNOWN')}]：{detail}")
                elif kind == "RUN_FINISHED":
                    outcome = event.get("outcome")
                    if not isinstance(outcome, dict) or outcome.get("type") != "success":
                        raise AgentCoreError("Agent 已结束，但 outcome.type 不是 success。")
                    return AgentReply(thread_id, run_id, messages)
            raise AgentCoreError("连接已关闭，但未收到成功结束事件；本次不能判定成功。")
    except error.HTTPError as exc:
        with exc:
            try:
                body = exc.read(2048).decode("utf-8", errors="replace").replace(api_key, "[REDACTED]")
            except (OSError, HTTPException):
                body = "（未能完整读取错误响应）"
        hints = {401: "检查 Endpoint API Key", 403: "检查访问权限和 API Key",
                 404: "检查 Endpoint 和接口路径", 429: "检查配额或并发限制",
                 503: "检查 Agent 实例是否就绪"}
        hint = hints.get(exc.code, "检查响应中的错误说明")
        raise AgentCoreError(f"HTTP {exc.code}：{hint}。\n{body}") from exc
    except (socket.timeout, TimeoutError) as exc:
        raise AgentCoreError("连接或读取超时；云端任务可能仍在运行，本脚本不会自动重试。") from exc
    except error.URLError as exc:
        raise AgentCoreError("网络连接失败：" + str(exc.reason).replace(api_key, "[REDACTED]")) from exc
    except (OSError, UnicodeError, HTTPException) as exc:
        raise AgentCoreError("连接或解码失败：" + str(exc).replace(api_key, "[REDACTED]")) from exc


def stop_agent(endpoint, api_key, thread_id, run_id, timeout=3):
    """Explicit AG-UI stop; closing SSE alone does not stop cloud computation."""
    req = request.Request(agent_url(endpoint) + '/stop',
                          data=json.dumps(dict(threadId=thread_id, runId=run_id)).encode('utf-8'),
                          headers={'Content-Type':'application/json', 'x-api-key':api_key,
                                   'X-AgentCore-Session-ID':thread_id}, method='POST')
    try:
        with request.build_opener(_NoRedirect()).open(req, timeout=timeout) as response:
            return response.status == 204
    except (OSError, error.URLError, HTTPException):
        return False
