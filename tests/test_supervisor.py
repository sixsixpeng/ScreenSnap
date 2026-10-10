# -*- coding: utf-8 -*-
'''崩溃看护的判定逻辑（不启动真实进程，纯函数级）。'''
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import supervisor


class SupervisorTests(unittest.TestCase):
    def test_should_restart_only_on_abnormal_exit(self):
        self.assertFalse(supervisor.should_restart(0))
        self.assertTrue(supervisor.should_restart(1))
        self.assertTrue(supervisor.should_restart(3221225477))   # 0xC0000005 访问违规
        self.assertTrue(supervisor.should_restart(-1073741819))

    def test_restart_allowed_enforces_limit_inside_window(self):
        now = 1000.0
        self.assertTrue(supervisor.restart_allowed([], now))
        self.assertTrue(supervisor.restart_allowed([now - 1, now - 2], now))
        self.assertFalse(supervisor.restart_allowed([now - 1, now - 2, now - 3], now))

    def test_restart_allowed_forgets_old_restarts(self):
        now = 1000.0
        old = [now - 301, now - 302, now - 303]
        self.assertTrue(supervisor.restart_allowed(old, now))


    def test_supervisor_restarts_abnormal_child_then_gives_up(self):
        '''真跑一个必崩的子进程：应重启到上限次数后放弃，并返回退出码。'''
        import os
        import tempfile
        import supervisor as sup

        with tempfile.TemporaryDirectory() as folder:
            marker = os.path.join(folder, 'runs.txt')
            snippet = ("open(r'" + marker + "','a').write('x'); import os; os._exit(3)")
            code = sup.main([], command=[sys.executable, '-c', snippet],
                            limit=2, window=60.0, backoff=0)
            runs = len(open(marker, encoding='utf-8').read())
        self.assertEqual(code, 3)          # 返回最后一次的异常退出码
        self.assertEqual(runs, 2)          # 初次 + 1 次重启（limit=2 表示最多 1 次重启），之后放弃

    def test_supervisor_exits_with_child_on_normal_exit(self):
        '''子进程正常退出时看护一起退出，且不重启。'''
        import os
        import tempfile
        import supervisor as sup

        with tempfile.TemporaryDirectory() as folder:
            marker = os.path.join(folder, 'runs.txt')
            snippet = ("open(r'" + marker + "','a').write('x'); import sys; sys.exit(0)")
            code = sup.main([], command=[sys.executable, '-c', snippet],
                            limit=2, window=60.0, backoff=0)
            runs = len(open(marker, encoding='utf-8').read())
        self.assertEqual(code, 0)
        self.assertEqual(runs, 1)          # 只跑一次，不再拉起

if __name__ == '__main__':
    unittest.main()
