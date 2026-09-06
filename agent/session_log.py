# -*- coding: utf-8 -*-
"""会话运行明细（思考过程 / token / 工具调用）：按天 JSONL，供控制台「运行明细」查看。

每条 append 立即落盘（进程被杀不丢）；recent(limit) 读今天 + 昨天文件尾部
（最多 200 条），按时间倒序返回。
"""
from __future__ import annotations

import json
import os

_DAYS = 2
_MAX_SCAN = 200


class SessionLog:
    def __init__(self, data_dir: str):
        self.dir = os.path.join(data_dir, "sessions")

    def append(self, entry: dict):
        try:
            os.makedirs(self.dir, exist_ok=True)
            day = str(entry.get("ts") or "").split("T")[0] or os.path.splitext(os.path.basename(__file__))[0]
            path = os.path.join(self.dir, "%s.jsonl" % day)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def recent(self, limit: int = 30):
        """读最近 limit 条（时间倒序），今天 + 昨天两个文件尾部。"""
        import datetime
        today = datetime.date.today()
        days = [(today - datetime.timedelta(days=i)).isoformat() for i in range(_DAYS)]
        total = []
        for day in days:
            path = os.path.join(self.dir, "%s.jsonl" % day)
            try:
                if not os.path.exists(path):
                    continue
                with open(path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                for line in lines[-_MAX_SCAN:]:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        total.append(json.loads(line))
                    except Exception:
                        continue
            except Exception:
                continue
        return sorted(total, key=lambda e: str(e.get("ts") or ""), reverse=True)[: max(1, min(limit, 100))]
