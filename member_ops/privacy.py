"""隐私层：数据发给云端大模型之前做去标识化和字段最小化，回答回来再还原。

- 卡号换成会话内的临时代号（如 M-0007），云端模型看不到真实卡号；
- 去掉精确年龄、入会日期、性别等回答运营问题用不到的个人字段；
- strict 模式下，云端模型只能拿到汇总数据，拿不到单个会员明细。
本地模型（Ollama）不经过这一层，数据本来就不出本机。
"""

from __future__ import annotations

import re

ALIAS_RE = re.compile(r"M-\d{4}")
ID_KEYS = {"customer_id", "会员"}
# 精确到个人的画像字段：云端回答运营问题用不到
DROP_KEYS = {"age", "年龄", "gender", "性别", "reg_date", "入会时间", "birth", "出生日期"}
# strict 模式下不给云端模型的、按人返回明细的工具
PER_MEMBER_TOOLS = {"get_member_profile", "get_draft_context", "list_guide_tasks"}


class Redactor:
    def __init__(self, member_ids, strict: bool = False):
        self.member_ids = member_ids          # 支持 `in` 判断即可（如 pandas Index）
        self.strict = strict
        self.to_alias: dict[str, str] = {}
        self.to_real: dict[str, str] = {}

    def alias(self, cid: str) -> str:
        cid = str(cid)
        if cid not in self.to_alias:
            a = f"M-{len(self.to_alias) + 1:04d}"
            self.to_alias[cid], self.to_real[a] = a, cid
        return self.to_alias[cid]

    # ---- 发给模型的方向 ----

    def alias_text(self, text: str) -> str:
        """用户消息里出现的真实卡号换成代号。"""
        return re.sub(r"[0-9A-Za-z]{3,16}", lambda m: self.alias(m.group(0)) if m.group(0) in self.member_ids else m.group(0), text)

    def redact(self, obj, key: str | None = None):
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if k in DROP_KEYS:
                    continue
                if k == "members" and self.strict:   # strict：名单只给人数，不给明细
                    continue
                out[k] = self.redact(v, k)
            return out
        if isinstance(obj, list):
            return [self.redact(v, key) for v in obj]
        if isinstance(obj, str) and (key in ID_KEYS or obj in self.member_ids):
            return self.alias(obj)
        return obj

    def check_tool(self, name: str) -> None:
        if self.strict and name in PER_MEMBER_TOOLS:
            raise ValueError("严格隐私模式：云端模型不能读取单个会员的明细，请改用汇总类工具（如 get_overview、segment_breakdown）")

    # ---- 回到本地的方向 ----

    def unalias(self, obj):
        if isinstance(obj, dict):
            return {k: self.unalias(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.unalias(v) for v in obj]
        if isinstance(obj, str):
            return ALIAS_RE.sub(lambda m: self.to_real.get(m.group(0), m.group(0)), obj)
        return obj
