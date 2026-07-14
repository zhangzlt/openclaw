"""
智能体测试剧本缓存

"固定剧本优先"：每个智能体的成功测试策略缓存为剧本，
下次直接确定性回放，无需 LLM 参与。
"""

import json
import copy
from pathlib import Path
from datetime import datetime, timezone, timedelta

PLAYBOOK_DIR = Path(__file__).parent.parent / "playbooks"
CACHE_FILE = PLAYBOOK_DIR / "cache.json"
LOGS_DIR = PLAYBOOK_DIR / "logs"
MAX_VERSIONS = 5          # 保留最近 N 个版本的剧本
MAX_CONSECUTIVE_FAILS = 3  # 连续失败 N 次后放弃缓存，走 LLM 重规划
CST = timezone(timedelta(hours=8))


def _now() -> str:
    return datetime.now(CST).isoformat()


class PlaybookCache:
    """智能体测试剧本持久化缓存。"""

    def __init__(self):
        PLAYBOOK_DIR.mkdir(parents=True, exist_ok=True)
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    # ── 文件 IO ──

    def _load(self) -> dict:
        if CACHE_FILE.exists():
            try:
                return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save(self):
        CACHE_FILE.write_text(json.dumps(self._data, ensure_ascii=False, indent=2),
                              encoding="utf-8")

    # ── 读 ──

    def get(self, agent_id: int) -> dict | None:
        """获取智能体的最新有效剧本（不含版本历史）。"""
        entry = self._data.get(str(agent_id))
        if not entry:
            return None
        playbook = copy.deepcopy(entry.get("playbook"))
        if not playbook:
            return None
        # 注入元数据但剥离版本历史以减小体积
        playbook["_meta"] = {
            "last_success": entry.get("last_success"),
            "success_count": entry.get("success_count", 0),
            "fail_count": entry.get("fail_count", 0),
        }
        return playbook

    def should_use_cache(self, agent_id: int) -> bool:
        """判断是否应该尝试命中缓存（连续失败过多则放弃）。"""
        entry = self._data.get(str(agent_id))
        if not entry:
            return False
        if not entry.get("playbook"):
            return False
        consecutive = entry.get("consecutive_fails", 0)
        return consecutive < MAX_CONSECUTIVE_FAILS

    def all_ids(self) -> list:
        return [int(k) for k in self._data if self._data[k].get("playbook")]

    # ── 写 ──

    def set(self, agent_id: int, playbook: dict):
        """缓存成功剧本。"""
        key = str(agent_id)
        entry = self._data.get(key, {})
        entry["playbook"] = copy.deepcopy(playbook)
        entry["success_count"] = entry.get("success_count", 0) + 1
        entry["consecutive_fails"] = 0
        entry["last_success"] = _now()

        # 版本历史
        versions = entry.get("versions", [])
        version = {"timestamp": _now(), "playbook": copy.deepcopy(playbook)}
        versions.append(version)
        entry["versions"] = versions[-MAX_VERSIONS:]

        self._data[key] = entry
        self._save()

    def mark_failed(self, agent_id: int, error: str = ""):
        """标记剧本执行失败。"""
        key = str(agent_id)
        entry = self._data.get(key, {})
        entry["fail_count"] = entry.get("fail_count", 0) + 1
        entry["consecutive_fails"] = entry.get("consecutive_fails", 0) + 1
        entry["last_fail"] = _now()
        entry["last_error"] = error[:500] if error else ""
        self._data[key] = entry
        self._save()

    def mark_skip(self, agent_id: int, reason: str = ""):
        """标记智能体为不可测试（需要特殊登录等），避免每次都走 LLM 重规划。"""
        key = str(agent_id)
        entry = self._data.get(key, {})
        entry["playbook"] = {"strategy": "skip", "reasoning": reason}
        entry["consecutive_fails"] = 0
        entry["last_success"] = _now()
        self._data[key] = entry
        self._save()

    # ── 日志 ──

    def save_log(self, agent_id: int, run_id: str, log_data: dict):
        """保存本次执行日志。"""
        log_file = LOGS_DIR / f"{agent_id}_{run_id}.json"
        log_file.write_text(json.dumps(log_data, ensure_ascii=False, indent=2),
                            encoding="utf-8")

    # ── 统计 ──

    def stats(self) -> dict:
        """缓存统计信息。"""
        total = len(self._data)
        cached = sum(1 for e in self._data.values()
                     if e.get("playbook") and e["playbook"].get("strategy") != "skip")
        skipped = sum(1 for e in self._data.values()
                      if e.get("playbook") and e["playbook"].get("strategy") == "skip")
        return {
            "total_entries": total,
            "cached_playbooks": cached,
            "marked_skip": skipped,
            "agents_without_playbook": total - cached - skipped,
        }
