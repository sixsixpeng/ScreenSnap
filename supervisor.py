# -*- coding: utf-8 -*-
'''ScreenSnap 崩溃看护：异常退出时自动重启，正常退出时一起退出。'''
import datetime
import os
import subprocess
import sys
import time


def _default_root():
    '''日志放在程序根目录：源码模式是仓库根，打包后是 exe 所在目录。

    不能用 Path(__file__).parent：onedir 下它指向 _internal，onefile 下指向临时解包目录，
    日志会写进用户找不到、重启后还会消失的位置。
    '''
    from core.startup import project_root

    return project_root()


ROOT = _default_root()
LIMIT = 3
WINDOW = 300.0
BACKOFF = 1.0
LOG_NAME = 'supervisor.log'
# 提权/降权拉起的新进程会带这两个参数；子进程不需要它们（看护已经等过锁）。
PERMISSION_MARKERS = ('--elevated', '--standard')


def wait_for_previous_instance(timeout_ms=20000):
    '''等待旧实例退出并释放单实例锁。

    提权/降权重启时新进程会先于旧进程退出启动；旧进程要先保存贴图会话，
    这里最多等 timeout_ms，避免新实例抢锁失败后直接提示「已经在运行」。
    '''
    try:
        from PySide6.QtCore import QLockFile
        from core.path_utils import data_dir

        lock = QLockFile(str(data_dir() / 'screensnap.lock'))
        if lock.tryLock(int(timeout_ms)):
            lock.unlock()
            return True
    except (ImportError, OSError) as error:
        log('等待单实例锁失败: ' + str(error))
    return False


def child_argv(argv):
    '''传给子进程的参数：去掉只对启动方式有意义的权限标记。'''
    return [arg for arg in argv if arg not in PERMISSION_MARKERS]


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
    '''子进程命令行。

    打包（PyInstaller/Nuitka）后 `sys.executable` 就是 exe 本身，**不能**再拼 main.py ——
    那会变成 `ScreenSnap.exe main.py …`，多出一个无意义的位置参数（onefile 下 ROOT 还是
    临时解包目录，该路径并不存在）。具体拼法统一放在 core.startup.launch_command。
    '''
    from core.startup import launch_command

    return launch_command(argv)


def main(argv=None, command=None, limit=LIMIT, window=WINDOW, backoff=BACKOFF):
    '''看护循环：拉起子进程；异常退出则重启，达上限则停止。参数可注入以便测试。'''
    argv = list(sys.argv[1:] if argv is None else argv)
    handoff = any(marker in argv for marker in PERMISSION_MARKERS)
    if handoff:
        waited = wait_for_previous_instance()
        log('权限切换启动：等待旧实例释放单实例锁' + ('成功' if waited else '超时'))
        argv = child_argv(argv)
    command = default_command(argv) if command is None else list(command)
    history = []
    restarted_from = None
    while True:
        started = time.monotonic()
        env = None
        env = dict(os.environ)
        env["SCREENSNAP_SUPERVISED"] = "1"
        if restarted_from is not None:
            # 崩溃复现开关只对首次启动生效，重启出来的进程不再自崩。
            env.pop("SCREENSNAP_CRASH_TEST", None)
            env[chr(83) + chr(67) + chr(82) + chr(69) + chr(69) + chr(78) + chr(83) + chr(78) + chr(65) + chr(80) + chr(95) + chr(82) + chr(69) + chr(83) + chr(84) + chr(65) + chr(82) + chr(84) + chr(69) + chr(68)] = str(restarted_from)
        # 留痕（2026-10-11）：把真正拉起的命令行写进 supervisor.log —— 打包后这是唯一能
        # 确认「子进程命令是否正确」（有没有多拼 main.py）的证据，见 default_command 的说明。
        log('子进程命令: ' + subprocess.list2cmdline(command))
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
