"""Run: python test_connection.py (reads .env beside this file)."""

import argparse
import sys
import uuid
from pathlib import Path

from agentcore_client import AgentCoreError, call_agent, load_config


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="AgentCore 连通性测试，不调用 APS 求解器。")
    parser.add_argument("--message", default="这是连通性测试，请不要调用工具，仅回复：APS Agent 连接成功。")
    parser.add_argument("--thread-id", help="续聊时传入上次显示的 threadId；默认创建新会话")
    parser.add_argument("--timeout", type=float, default=90, help="连接/单次读取超时秒数，默认 90")
    args = parser.parse_args()
    thread_id = args.thread_id or "aps-demo-" + uuid.uuid4().hex
    run_id = "run-" + uuid.uuid4().hex
    try:
        endpoint, api_key = load_config(Path(__file__).with_name(".env"))
        print(f"threadId: {thread_id}\nrunId: {run_id}", flush=True)
        print("正在调用 Agent，等待 SSE 执行结果……", flush=True)
        reply = call_agent(endpoint, api_key, args.message, thread_id=thread_id,
                           run_id=run_id, timeout=args.timeout)
        print("\n连通成功：收到 RUN_FINISHED，outcome.type=success。")
        print("\nAgent 回复：\n" + (reply.text or "（本次成功结束，但没有文本回复）"))
        return 0
    except AgentCoreError as exc:
        print("\n测试未成功：" + str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n本地接收已中断；这不代表云端任务已停止。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
