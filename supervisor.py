# -*- coding: utf-8 -*-
'''ScreenSnap 崩溃看护：异常退出时自动重启，正常退出时一起退出。'''
import datetime
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent
LIMIT = 3
WINDOW = 300.0
BACKOFF = 1.0
LOG_NAME = 'supervisor.log'


def should_restart(returncode):
    '''0 = 用户主动退出（托盘退出/正常关闭）→ 不重启；非 0 = 异常退出 → 重启。'''
    return bool(returncode)


def restart_allowed(history, now, limit=LIMIT, window=WINDOW):
    '''防崩溃循环：window 秒内的重启次数少于 limit 才允许再重启。'''
    recent = [t for t in history if now - t <= window]
    return len(recent) < limit


def log_path():
    return ROOT / 'logs' / LOG_NAME


def log(message):
    stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = stamp + ' ' + message
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'a', encoding='utf-8') as handle:
            print(line, file=handle)
    except OSError:
        pass
    print(line, flush=True)


def default_command(argv):
    return [sys.executable, str(ROOT / 'main.py'), *argv]


def main(argv=None, command=None, limit=LIMIT, window=WINDOW, backoff=BACKOFF):
    '''看护循环：拉起子进程；异常退出则重启，达上限则停止。参数可注入以便测试。'''
    argv = list(sys.argv[1:] if argv is None else argv)
    command = default_command(argv) if command is None else list(command)
    history = []
    restarted_from = None
    while True:
        started = time.monotonic()
        env = None
        if restarted_from is not None:
            env = dict(os.environ)
            env[chr(83) + chr(67) + chr(82) + chr(69) + chr(69) + chr(78) + chr(83) + chr(78) + chr(65) + chr(80) + chr(95) + chr(82) + chr(69) + chr(83) + chr(84) + chr(65) + chr(82) + chr(84) + chr(69) + chr(68)] = str(restarted_from)
        code = subprocess.run(command, env=env).returncode
        if not should_restart(code):
            log('子进程正常退出（code=0），看护结束')
            return 0
        restarted_from = code
        now = time.monotonic()
        history.append(now)
        if not restart_allowed(history, now, limit, window):
            log('子进程异常退出（code=' + str(code) + '），' + str(int(window)) + ' 秒内连续异常退出 ' + str(len(history)) + ' 次（上限 ' + str(limit) + ' 次重启），停止自动重启')
            return code
        log('子进程异常退出（code=' + str(code) + '，运行 ' + ('%.1f' % (now - started)) + ' 秒），' + ('%.1f' % backoff) + ' 秒后第 ' + str(len(history)) + ' 次重启')
        if backoff:
            time.sleep(backoff)


if __name__ == '__main__':
    sys.exit(main())
