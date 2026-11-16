"""运行配置：读取项目根目录的 .env，以及线上部署用到的开关。

.env 只在本地使用（已被 .gitignore 排除）；线上部署时同名变量放在平台的环境变量里。
已经存在的环境变量不会被 .env 覆盖。
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value:
            os.environ.setdefault(key, value)


load_dotenv()


def _flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


# 演示模式：公开访问时使用。访客的操作写到临时目录，并定时自动恢复，不会改乱演示数据
DEMO_MODE = _flag("DEMO_MODE")
DEMO_RESET_HOURS = float(os.environ.get("DEMO_RESET_HOURS", "6"))

# 处理后的会员数据；线上由启动脚本从私有数据集下载到这里
PROCESSED_DIR = Path(os.environ.get("PROCESSED_DIR") or ROOT / "data" / "processed")

# 活动、导购任务、支付宝请求等运行状态
STATE_DIR = Path(os.environ.get("STATE_DIR") or ("/tmp/member-ops-state" if DEMO_MODE else ROOT / "data"))

# 隐私模式：standard = 发给云端模型前把卡号换成临时代号、去掉非必要字段；
# strict = 云端模型只能看到汇总数据，拿不到单个会员明细
PRIVACY_MODE = os.environ.get("PRIVACY_MODE", "standard").strip().lower()

# 大模型调用额度保护（防止免费额度被刷光）
CHAT_PER_MINUTE = int(os.environ.get("CHAT_PER_MINUTE", "6"))      # 每个访客每分钟最多提问次数
LLM_DAILY_LIMIT = int(os.environ.get("LLM_DAILY_LIMIT", "300"))     # 全站每天最多调用大模型的请求数
