"""CLI end-to-end smoke run, opt-in real Agent API via --agent."""
import argparse
import json
import time
from controller import Manager

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--agent', action='store_true')
    parser.add_argument('--rounds', type=int, default=2)
    args = parser.parse_args()
    m = Manager()
    j = m.start(dict(mode='agent' if args.agent else 'baseline', max_rounds=args.rounds,
                     time_budget=300, patience=2), '保障必须交付与在制完成，改善产线均衡度及OEE，控制无需求补货。')
    print('job=' + j['id'], flush=True)
    seen = 0
    while True:
        j = m.get(j['id'])
        for e in j['events'][seen:]:
            print(e['phase'] + ': ' + e['text'], flush=True)
        seen = len(j['events'])
        if j['status'] not in ('queued', 'running'):
            print(json.dumps(dict(status=j['status'], best_round=j['best_round'], reason=j['stop_reason']), ensure_ascii=False), flush=True)
            raise SystemExit(0 if j['best_round'] is not None and j['status'] == 'completed' else 1)
        time.sleep(.5)
