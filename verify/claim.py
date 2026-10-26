#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# glacc-audit 领奖请求验证

import json
import time
import argparse
import urllib.request
import urllib.error
import ssl

BASE_URL = 'https://game-xacc.xunlei.com'
CTX = ssl.create_default_context()

# 凭证从抓包里抄，占位符换成自己的
HDR = {
    'content-type': 'application/json',
    'accept': 'application/json, text/plain, */*',
    'appid': '<APPID>',
    'package_name': '<PACKAGE>',
    'x-client-type': '<CLIENT_TYPE>',
    'userid': '<USERID>',
    'peerid': '<PEERID>',
    'x-device-id': '<DEVICE_ID>',
    'x-guid': '<DEVICE_ID>',
    'authorization': 'Bearer <TOKEN>',
    'user-agent': '<USER_AGENT>',
}

TASK_LIST = '/xlppc.gacs/api/gxsdn/act/advert/mobileGLTaskList?appid=8'
CLAIM = '/xlppc.gacs/api/gxsdn/act/advert/mobileTaskSpark/claim'
WALLET = '/xlppc.gacs/api/gxsdn/gold/get_user_wallet'


def call(path, body=None, method='POST', timeout=30):
    url = BASE_URL + path
    data = json.dumps(body, separators=(',', ':')).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=HDR, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            return r.status, json.loads(r.read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode('utf-8', 'replace'))
        except Exception:
            return e.code, {}
    except Exception as e:
        return -1, {'err': str(e)}


def get_tasks():
    st, j = call(TASK_LIST, None, 'GET')
    if j.get('code') != 0:
        raise SystemExit('拉列表失败 HTTP %s %s' % (st, json.dumps(j, ensure_ascii=False)[:300]))
    return j['data']


def get_wallet():
    st, j = call(WALLET, None, 'GET')
    return j.get('data', {})


def claim(tasks, master_id, task_id):
    payload = {
        'action': 'subtask_return',
        'masterTaskId': master_id,
        'taskId': task_id,
        'requestId': str(int(time.time() * 1000)),
        'snapshotId': tasks['snapshotId'],     # 一次性，领完得重拉
        'strategyId': tasks.get('strategyId', ''),
    }
    return call(CLAIM, payload)


def flatten(tasks):
    out = []
    for m in tasks.get('list', []):
        for s in (m.get('taskDetailList') or []):
            if s.get('finish'):
                continue
            if not s.get('advertLink'):       # 这类另有判定
                continue
            out.append((m['masterTaskId'], s))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='只看列表')
    ap.add_argument('--once', action='store_true', help='只打一个')
    ap.add_argument('--interval', type=float, default=2.0, help='间隔秒数')
    args = ap.parse_args()

    w0 = get_wallet()
    print('起始 score=%s acc=%s' % (w0.get('score'), w0.get('remain_acc_second')))

    tasks = get_tasks()
    print('snapshotId=%s strategyId=%s' % (tasks['snapshotId'], tasks.get('strategyId')))

    jobs = flatten(tasks)
    print('可领 %d 个' % len(jobs))
    for mid, s in jobs:
        print('  master=%-4s task=%-4s %-12s %6s' % (
            mid, s['taskId'], s.get('taskName', ''), s.get('total', 0)))

    if args.dry_run:
        return
    if args.once:
        jobs = jobs[:1]

    ok = 0
    gmin = 0
    gcoin = 0

    for i, (mid, s) in enumerate(jobs, 1):
        st, r = claim(tasks, mid, s['taskId'])
        code = r.get('code')
        d = r.get('data') or {}

        if code == 0:
            ok += 1
            gmin += d.get('rewardMinutes', 0)
            gcoin += d.get('rewardCoin', 0)
            print('[%2d] OK   task=%-4s +%s分 +%s币 %s' % (
                i, s['taskId'], d.get('rewardMinutes'), d.get('rewardCoin'), s.get('taskName', '')))
        else:
            print('[%2d] FAIL task=%-4s code=%s %s' % (
                i, s['taskId'], code, r.get('result', '')))

        tasks = get_tasks()
        if i < len(jobs):
            time.sleep(args.interval)

    w1 = get_wallet()
    print()
    print('成功 %d/%d  +%s分 +%s币' % (ok, len(jobs), gmin, gcoin))
    print('score %s -> %s' % (w0.get('score'), w1.get('score')))
    print('acc   %s -> %s' % (w0.get('remain_acc_second'), w1.get('remain_acc_second')))


if __name__ == '__main__':
    main()
