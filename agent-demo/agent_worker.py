"""One bounded cloud proposal. Keys remain inside this process, never in job artifacts."""
import json
import sys
import time
from pathlib import Path
from agentcore_client import load_config, call_agent

BASE = Path(__file__).resolve().parent


def extract_json(text):
    # Accept one JSON object, optionally fenced; do not execute any generated code.
    stripped = text.strip()
    if stripped.startswith('```'):
        stripped = stripped.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    value = json.loads(stripped)
    if not isinstance(value, dict):
        raise ValueError('Agent 必须返回一个 JSON 对象。')
    return value


def propose(payload):
    endpoint, key = load_config(BASE / '.env')
    instructions = (BASE / 'prompts/system.md').read_text(encoding='utf-8') + '\n' + (BASE / 'skills/feed-planning/SKILL.md').read_text(encoding='utf-8')
    message = instructions + '\n\n现在完成一次排产参数提案。无需自行推演最优排程，仅选择一个值得测试的小改动；正确性由外部程序检验。最多调整3个物料，reason最多80字，expected_effect最多40字。直接输出短JSON。\n<business_snapshot>\n' + json.dumps(payload['context'], ensure_ascii=False, separators=(',', ':')) + '\n</business_snapshot>\n/no_think'
    last_stage = None
    def progress(event):
        nonlocal last_stage
        kind = event.get('type', '')
        stage = ('云端已接收' if kind == 'RUN_STARTED' else '模型思考中' if kind.startswith('REASONING')
                 else '生成提案中' if kind.startswith('TEXT_MESSAGE') else '云端工具执行中' if kind.startswith('TOOL_CALL') else None)
        if stage and stage != last_stage and payload.get('progress_path'):
            last_stage = stage
            Path(payload['progress_path']).write_text(json.dumps(dict(stage=stage)), encoding='utf-8')
    reply = call_agent(endpoint, key, message,
                       thread_id=payload['thread_id'], run_id=payload.get('run_id'), timeout=180, on_event=progress)
    # Last complete assistant message is the final answer; intermediate chatter is not a proposal.
    messages = [s for s in reply.messages.values() if s.strip()]
    if not messages:
        raise ValueError('Agent 成功结束但未输出排产参数。')
    proposal = extract_json(messages[-1])
    return dict(proposal=proposal, thread_id=reply.thread_id, run_id=reply.run_id)


if __name__ == '__main__':
    try:
        print(json.dumps({'ok': True, 'result': propose(json.load(sys.stdin))}, ensure_ascii=False))
    except Exception as exc:
        # Avoid printing endpoint, credentials, server tracebacks or full raw cloud responses.
        detail = str(exc)
        try:
            endpoint, key = load_config(BASE / '.env')
            detail = detail.replace(key, '[REDACTED]').replace(endpoint, '[ENDPOINT]')
        except Exception:
            pass
        print(json.dumps({'ok': False, 'error': detail[:1000]}, ensure_ascii=False))
        sys.exit(1)
