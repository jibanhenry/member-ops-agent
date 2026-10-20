"""会员运营 Agent：大模型 + 工具调用。

后端按优先级自动选择（也可用环境变量 LLM_BACKEND=ollama/claude/offline 指定）：
1. ollama  —— 本地开源模型（默认 qwen3.5-9b-local），免费、数据不出本机
2. claude  —— 配置了 ANTHROPIC_API_KEY 时可用
3. offline —— 规则引擎兜底，保证没有任何模型时原型也能用
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from .segments import PLAYBOOK_DIR
from .tools import TOOL_LABELS, TOOL_SPECS, MemberStore, run_tool

CLAUDE_MODEL = "claude-opus-5-5"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.5-9b-local")
OLLAMA_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "16384"))
MAX_TOOL_ROUNDS = 8


def build_system_prompt(store: MemberStore) -> str:
    methodology = (PLAYBOOK_DIR / "methodology.md").read_text(encoding="utf-8")
    ds = store.report["dataset"]
    return f"""你是一位资深的会员运营顾问，服务对象是门店老板和运营人员。你通过工具读取商家会员系统的分析结果，帮助他们看清会员结构、发现流失风险、制定可执行且算得过账的运营动作。

<methodology>
{methodology}
</methodology>

<data_context>
- 数据：{ds['name']}（{ds['license']}）。{ds['description']}
- 观察日：{ds['snapshot_date']}，会员 {ds['n_members']} 人，消费记录 {ds['n_orders']} 笔，金额单位 {ds['currency']}
- 生命周期、价值分层和策略来自商家的运营 Playbook；流失概率和未来消费来自模型预测，模型可信度可用 get_model_report 查询
- 价值分层综合历史消费和预测未来一年消费；"价值下滑"指历史排名高但预测排名低，"潜力上升"相反
- 每位会员已排好一个下一步动作（人工回访按导购产能排队、补货窗口提醒、临近大促并入大促推送等），用 get_action_plan 看全局安排
- 单人成本上限 = 预测未来一年消费 × 策略规定比例
- 权益分四组：高价值会员一律不做价格减免；"羊毛党"（几乎只在打折时买、且不是高价值）发券回不了本就不发；价格敏感的会员才用让利型权益；其余给体验和小样
- 每位会员有付费预期跟踪：检查点上预测的消费次数 vs 实际，明显不够的是"未达预期"，附原因，用 find_members(shortfall=true) 圈选
- 算账口径：用户 ROI =（预测消费 × 实际毛利率 − 真实运营成本）÷ 真实运营成本。实际毛利率按会员自己的折扣率折算；真实成本 = 每人固定成本 + 按下一步动作计价的动作投入（导购回访、定向触达、补货提醒等）+ 权益（让利全额、赠品成本价），不是券面额。用 get_budget_plan 看各人群 ROI 和整体是否为正
- 两个 ROI 管不同的事：用户 ROI 说明"这个人值不值得花这些钱"，只用来定成本上限和预算倾斜；"这笔钱有没有带来额外消费"要看对照实验的增量 ROI（实验组和对照组'实际 − 预测'的差距），只有它能决定一个动作要不要继续。没有实验数据的人群要说"待验证"，不要拿用户 ROI 当作活动效果
- 活动用 create_campaign 登记并留对照组，用 evaluate_campaign 评估；回放演示的活动没有真实执行，不能当作效果证据
- 高价值画像和拉新来源用 get_value_portrait 查询
</data_context>

工作方式：
- 先用工具取数再下结论，数字以工具返回为准，不要凭空估计；工具里没有的信息（如会员姓名、投诉内容）直接说明没有。
- 给运营方案时写清：目标人群和人数、具体动作、时机、权益和成本、衡量指标，并用 simulate_campaign 算账。核销率、权益金额等假设要明确标注为假设。
- 方案里的权益成本不要超过成本上限；时机要考虑导购产能和近期大促档期，不要建议超过频控的触达。
- 不要给高价值会员建议折扣、满减等价格减免；给羊毛党发券前先用 simulate_campaign(incentive_group="deal_hunter", incentive_type="price_cut") 算账，ROI 为负就不发。
- 拉新和留存的预算优先给生命周期价值高的人群，整体用户 ROI 必须为正；建议新活动时提醒留对照组，并说明以该人群规模能测出多大的提升。
- 用中文回答，结论先行，适度使用小标题和列表，金额带单位。"""


def _exec_tool(store: MemberStore, name: str, args: dict, trace: list) -> tuple[str, bool]:
    """执行工具，返回 (结果文本, 是否出错)。参数错误回传给模型自行修正。"""
    try:
        output = run_tool(store, name, dict(args or {}))
        trace.append({"tool": name, "input": args})
        return json.dumps(output, ensure_ascii=False), False
    except Exception as e:
        trace.append({"tool": name, "input": args, "error": str(e)})
        return f"Error: {e}", True


def _tool_start(name: str, args: dict) -> dict:
    return {"type": "tool_start", "tool": name, "label": TOOL_LABELS.get(name, name), "input": args}


def _tool_end(name: str, is_error: bool, text: str) -> dict:
    event = {"type": "tool_end", "tool": name, "ok": not is_error}
    if is_error:
        event["error"] = text
    return event


# ---------- 本地模型：Ollama ----------

def ollama_available() -> bool:
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=2) as r:
            models = [m["name"] for m in json.load(r)["models"]]
        return any(m.split(":")[0] == OLLAMA_MODEL.split(":")[0] for m in models)
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return False


class OllamaBackend:
    name = "ollama"
    label = f"本地模型 {OLLAMA_MODEL}"

    def __init__(self, store: MemberStore, system: str):
        self.store = store
        # 本地小模型每秒只能生成十来个字，回答长度直接决定等待时间
        self.system = system + """
- 回答控制在 400 字以内，只给最关键的数字和动作；一个问题通常调用 1~2 次工具就够了，不要用不同参数反复查询同一件事。
- ROI、挽回收入、活动成本这类数字只能来自 simulate_campaign 或 get_budget_plan 的返回结果，没调用就不要写。"""
        self.tools = [{"type": "function", "function": {
            "name": t["name"], "description": t["description"], "parameters": t["input_schema"]}} for t in TOOL_SPECS]

    def _stream_chat(self, messages: list):
        """调用 Ollama 流式接口，逐块产出 message 片段（NDJSON，一行一块）。"""
        body = json.dumps({
            "model": OLLAMA_MODEL,
            "messages": [{"role": "system", "content": self.system}] + messages,
            "tools": self.tools,
            "stream": True,
            "keep_alive": "30m",  # 常驻内存，避免每次重新加载模型
            "think": False,  # 小模型关闭思考，响应快很多
            "options": {"num_ctx": OLLAMA_NUM_CTX, "temperature": 0.3},
        }).encode()
        req = urllib.request.Request(f"{OLLAMA_URL}/api/chat", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=600) as r:
            for line in r:
                if line.strip():
                    chunk = json.loads(line)
                    if "error" in chunk:
                        raise RuntimeError(chunk["error"])
                    yield chunk.get("message", {})

    def stream(self, history: list):
        trace = []
        for _ in range(MAX_TOOL_ROUNDS):
            content, calls = "", []
            for part in self._stream_chat(history):
                if part.get("content"):
                    content += part["content"]
                    yield {"type": "text", "delta": part["content"]}
                calls += part.get("tool_calls") or []
            history.append({"role": "assistant", "content": content, "tool_calls": calls})

            if not calls:
                answer = content.strip()
                if self._unverified_math(answer, trace):
                    yield {"type": "reset", "reason": "检测到未经测算的 ROI，正在调用测算工具重算…"}
                    history.append({"role": "user", "content": "你的回答里有 ROI/挽回收入/成本测算，但没有调用 simulate_campaign。"
                                    "请调用它拿到真实结果，再修正回答。"})
                    continue
                yield {"type": "done", "answer": answer, "tool_calls": trace, "mode": self.name}
                return

            for c in calls:
                fn = c["function"]
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    args = json.loads(args or "{}")
                yield _tool_start(fn["name"], args)
                text, is_error = _exec_tool(self.store, fn["name"], args, trace)
                yield _tool_end(fn["name"], is_error, text)
                history.append({"role": "tool", "tool_name": fn["name"], "content": text})
        yield {"type": "done", "answer": "分析步骤过多，已停止。请把问题拆小一些再问。", "tool_calls": trace, "mode": self.name}

    @staticmethod
    def _unverified_math(answer: str, trace: list) -> bool:
        """小模型常"心算"投入产出，这里拦截没有经过测算工具的 ROI 数字。"""
        claims = re.search(r"ROI|投入产出|回报率|挽回(收入|消费)", answer)
        return bool(claims) and not any(t["tool"] in ("simulate_campaign", "get_budget_plan") and "error" not in t for t in trace)


# ---------- 云端模型：Claude ----------

def claude_available() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    return (Path.home() / ".config" / "anthropic").exists()


class ClaudeBackend:
    name = "claude"
    label = f"Claude {CLAUDE_MODEL}"

    def __init__(self, store: MemberStore, system: str):
        import anthropic

        self.store = store
        self.system = system
        self.client = anthropic.Anthropic()

    def stream(self, history: list):
        trace = []
        for _ in range(MAX_TOOL_ROUNDS):
            response = self.client.beta.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=16000,
                system=self.system,
                tools=TOOL_SPECS,
                messages=history,
                output_config={"effort": "medium"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            # 保留完整 content（含 thinking 块）原样回传
            history.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "refusal":
                yield {"type": "done", "answer": "这个问题暂时答不了，请换个问法试试。", "tool_calls": trace, "mode": self.name}
                return
            if response.stop_reason != "tool_use":
                text = "".join(b.text for b in response.content if b.type == "text")
                if response.stop_reason == "max_tokens":
                    text += "\n\n（回答过长被截断）"
                yield {"type": "text", "delta": text}
                yield {"type": "done", "answer": text, "tool_calls": trace, "mode": self.name}
                return

            results = []
            for block in response.content:
                if block.type == "tool_use":
                    yield _tool_start(block.name, block.input)
                    text, is_error = _exec_tool(self.store, block.name, block.input, trace)
                    yield _tool_end(block.name, is_error, text)
                    result = {"type": "tool_result", "tool_use_id": block.id, "content": text}
                    if is_error:
                        result["is_error"] = True
                    results.append(result)
            history.append({"role": "user", "content": results})
        yield {"type": "done", "answer": "分析步骤过多，已停止。请把问题拆小一些再问。", "tool_calls": trace, "mode": self.name}


# ---------- Agent ----------

def pick_backend(store: MemberStore, system: str):
    choice = os.environ.get("LLM_BACKEND", "auto")
    if choice in ("auto", "ollama") and ollama_available():
        return OllamaBackend(store, system)
    if choice in ("auto", "claude") and claude_available():
        return ClaudeBackend(store, system)
    return None


class MemberOpsAgent:
    def __init__(self, store: MemberStore):
        self.store = store
        self.system = build_system_prompt(store)
        self.backend = pick_backend(store, self.system)
        self.sessions: dict[str, list] = {}

    @property
    def label(self) -> str:
        return self.backend.label if self.backend else "离线规则模式"

    def chat_stream(self, session_id: str, user_message: str):
        """产出事件流：text（增量文字）/ tool_start / tool_end / reset（清空本轮已显示文字）/ done。"""
        if self.backend is None:
            result = offline_answer(self.store, user_message)
            yield {"type": "text", "delta": result["answer"]}
            yield {"type": "done", **result}
            return
        history = self.sessions.setdefault(session_id, [])
        checkpoint = len(history)
        history.append({"role": "user", "content": user_message})
        try:
            yield from self.backend.stream(history)
        except Exception as e:  # 模型服务不可用时退回规则引擎，并撤销本轮半截对话
            del history[checkpoint:]
            result = offline_answer(self.store, user_message, note="助手暂时连不上，先给你快速查询的结果。")
            yield {"type": "reset", "reason": "大模型调用失败"}
            yield {"type": "text", "delta": result["answer"]}
            yield {"type": "done", **result}

    def chat(self, session_id: str, user_message: str) -> dict:
        """非流式：消费完事件流，返回最终结果。"""
        done = {}
        for event in self.chat_stream(session_id, user_message):
            if event["type"] == "done":
                done = event
        return {k: v for k, v in done.items() if k != "type"}


# ---------- 离线规则引擎 ----------

def _fmt_pct(v):
    return "—" if v is None else f"{v:.0%}"


def _fmt_times(v):
    return "—" if v is None else f"{v:.1f} 倍"


def _fmt_money(v, cur):
    return f"¥{v:,.0f}" if cur == "CNY" else f"{v:,.0f} {cur}"


def offline_answer(store: MemberStore, message: str, note: str | None = None) -> dict:
    """未配置 LLM 时按关键词路由到工具，用模板生成回答。"""
    cur = store.currency
    calls = []
    lines = [f"> {note or '助手目前只能做快速查询，以下是查询结果。'}", ""]

    # 会员卡号格式因数据集而异（数字或十六进制串），直接拿消息里的词去会员表里查
    cid = next((t for t in re.findall(r"[0-9A-Za-z]{3,16}", message) if t in store.members.index), None)
    if cid:
        p = run_tool(store, "get_member_profile", {"customer_id": cid})
        calls.append({"tool": "get_member_profile", "input": {"customer_id": cid}})
        pr, pred = p["画像"], p["预测"]
        h = store.churn_horizon
        lines += [f"### 会员 {p['customer_id']}",
                  f"- {pr['生命周期']} · {pr['价值分层']} · {pr['RFM 分群']}",
                  f"- {h} 天流失概率 {pred[f'{h}天流失概率'] or 0:.0%}，预测未来 90 天消费 {_fmt_money(pred['预测未来90天消费'], cur)}",
                  "- 流失信号：" + ("；".join(p["流失信号"]) or "暂无")]
        s = p["推荐策略"]
        if s:
            lines += [f"- 推荐策略：**{s['name']}** —— {s['goal']}"] + [f"  - {a}" for a in s["actions"]]
        return {"answer": "\n".join(lines), "tool_calls": calls, "mode": "offline"}

    if any(k in message for k in ("流失", "挽留", "召回", "风险", "预警")):
        f = run_tool(store, "find_members", {"stage": "at_risk", "value_tier": "high", "limit": 5})
        g = run_tool(store, "get_strategy", {"stage": "at_risk", "value_tier": "high"})
        calls += [{"tool": "find_members", "input": {"stage": "at_risk", "value_tier": "high"}},
                  {"tool": "get_strategy", "input": {"stage": "at_risk", "value_tier": "high"}}]
        lines += [f"### 高价值衰退预警会员：{f['matched']} 人，风险金额 {_fmt_money(f['matched_value_at_risk'], cur)}"]
        lines += [f"- 会员 {m['customer_id']}：{m['churn_reasons'] or '综合消费行为判断'}" for m in f["members"]]
        s = g["strategy"]
        lines += ["", f"**建议策略：{s['name']}**"] + [f"- {a}" for a in s["actions"]] + [f"- 频控：{s['guardrail']}"]
        return {"answer": "\n".join(lines), "tool_calls": calls, "mode": "offline"}

    if any(k in message for k in ("本周", "这周", "行动", "计划", "补货", "导购", "先联系", "大促", "档期")):
        a = run_tool(store, "get_action_plan", {})
        calls.append({"tool": "get_action_plan", "input": {}})
        mn, rp = a["manual"], a["replenishment"]
        lines += [f"### 行动计划（观察日 {a['snapshot_date']}）", "| 下一步动作 | 人数 | 风险金额 |", "|---|---|---|"]
        lines += [f"| {x['name']} | {x['members']:,} | {_fmt_money(x['value_at_risk'], cur)} |" for x in a["actions"]]
        lines += ["", f"- 需要导购人工跟进 {mn['demand']:,} 人，产能每周 {mn['capacity_per_week']} 人，"
                  f"排进 {mn['max_weeks']} 周的有 {mn['scheduled']:,} 人，其余 {mn['overflow_to_auto']:,} 人转自动触达",
                  f"- 处于补货窗口 {rp['in_window']:,} 人，按个人或品牌周期提醒",
                  "- 近期档期：" + ("；".join(f"{e['name']} {e['start']} 起（{e['days_until']} 天后）" for e in a["upcoming_events"][:3]) or "未配置")]
        return {"answer": "\n".join(lines), "tool_calls": calls, "mode": "offline"}

    if any(k in message for k in ("ROI", "roi", "预算", "羊毛", "算账", "投入产出", "值不值")):
        b = run_tool(store, "get_budget_plan", {})
        calls.append({"tool": "get_budget_plan", "input": {}})
        r = b["overall_roi"]
        lines += [f"### 运营预算与用户 ROI：整体 ROI {_fmt_times(r)}（{'为正' if r and r > 0 else '为负'}）",
                  f"- 一年真实运营成本 {_fmt_money(b['total_cost'], cur)}，会员预测毛利 {_fmt_money(b['total_pred_gross_profit'], cur)}",
                  f"- 高价值会员占成本 {_fmt_pct(b['high_value_cost_share'])}、占预测毛利 {_fmt_pct(b['high_value_profit_share'])}",
                  f"- 羊毛党 {b['deal_hunters']:,} 人，其中 {b['deal_hunters_no_coupon']:,} 人毛利抵不过发券成本，不发券",
                  "", "| 人群 | 运营成本 | 预测毛利 | ROI | 建议 |", "|---|---|---|---|---|"]
        lines += [f"| {x['segment']} | {_fmt_money(x['real_cost'], cur)} | {_fmt_money(x['pred_gross_profit'], cur)} | "
                  f"{_fmt_times(x['roi'])} | {x['advice']} |" for x in b["rows"]]
        return {"answer": "\n".join(lines), "tool_calls": calls, "mode": "offline"}

    if any(k in message for k in ("画像", "拉新", "高价值会员是", "长什么样")):
        v = run_tool(store, "get_value_portrait", {})
        calls.append({"tool": "get_value_portrait", "input": {}})
        lines += ["### 高价值会员明显集中的特征（TGI）"]
        lines += [f"- {x['dimension']}：{x['value']}，占高价值 {x['high_share']:.0%}，TGI {x['tgi']:.0f}" for x in v["portrait"]["over_index"][:6]]
        acq = v["acquisition"]
        lines += ["", f"### 拉新来源：首单金额段（{acq['cohort_note']}）"]
        lines += [f"- {x['group']}：首年人均消费 {_fmt_money(x['first_year_spend'], cur)}，现在高价值占比 {x['high_share']:.0%}"
                  for x in acq["by_first_amount"]]
        return {"answer": "\n".join(lines), "tool_calls": calls, "mode": "offline"}

    if any(k in message for k in ("活动", "实验", "对照", "有没有用", "额外消费", "增量")):
        cs = run_tool(store, "list_campaigns", {})["campaigns"]
        calls.append({"tool": "list_campaigns", "input": {}})
        if not cs:
            lines += ["还没有登记活动。在看板的「活动与对照实验」里新建活动会自动留出对照组，结束后对比两组'实际 − 预测'的差距，就能算出这笔钱带来的额外消费和增量 ROI。"]
        for c in cs:
            r = c.get("result") or {}
            lines += [f"### {c['name']}（{c['start_date']} 至 {c['end_date']}）",
                      f"- 实验组 {c['design']['treat_members']:,} 人，对照组 {c['design']['control_members']:,} 人"]
            if r:
                lines += [f"- 每人额外消费 {_fmt_money(r['incremental_per_member'], cur)}（95% 区间 {_fmt_money(r['ci95'][0], cur)} ~ {_fmt_money(r['ci95'][1], cur)}），"
                          f"{'显著' if r['significant'] else '不显著'}；增量 ROI {_fmt_times(r['incremental_roi'])}",
                          f"- 结论：{r['verdict']}"] + ([f"- {r['note']}"] if r.get("note") else [])
            else:
                lines += ["- 还没有评估"]
        return {"answer": "\n".join(lines), "tool_calls": calls, "mode": "offline"}

    mv = store.report.get("member_value")
    if mv and "非会员" in message:
        calls.append({"tool": "get_overview", "input": {}})
        m, n = mv["member"], mv["non_member"]
        lines += [f"### 会员 vs 非会员（{mv['period'][0]} 至 {mv['period'][1]}）",
                  "| | 会员 | 非会员 |", "|---|---|---|",
                  f"| 收入占比 | {m['revenue_share']:.0%} | {n['revenue_share']:.0%} |",
                  f"| 客单价 | {_fmt_money(m['aov'], cur)} | {_fmt_money(n['aov'], cur)} |",
                  f"| 件数/单 | {m['items_per_order']:.2f} | {n['items_per_order']:.2f} |",
                  "", f"会员订单数是非会员的 {m['orders'] / n['orders']:.0%}，客单价"
                  f"{'高' if m['aov'] >= n['aov'] else '低'} {abs(m['aov'] / n['aov'] - 1):.0%}，贡献了 {m['revenue_share']:.0%} 的收入。"]
        return {"answer": "\n".join(lines), "tool_calls": calls, "mode": "offline"}

    if any(k in message for k in ("准", "模型", "数据来源", "可信")):
        r = run_tool(store, "get_model_report", {})
        calls.append({"tool": "get_model_report", "input": {}})
        bt, cv = r["clv_backtest"], r["churn_validation"]
        lines += ["### 预测靠谱吗",
                  f"- 消费预测回看：之后 90 天购买 预测 {bt['pred_total_purchases']:.0f} / 实际 {bt['actual_total_purchases']} 次；"
                  f"收入 预测 {_fmt_money(bt['pred_total_revenue'], cur)} / 实际 {_fmt_money(bt['actual_total_revenue'], cur)}",
                  f"- 流失预警：判定风险最高的 10% 会员里 {cv['top10_precision']:.0%} 后来确实流失（整体 {cv['churn_rate']:.0%}）"]
        return {"answer": "\n".join(lines), "tool_calls": calls, "mode": "offline"}

    o = run_tool(store, "get_overview", {})
    calls.append({"tool": "get_overview", "input": {}})
    lines += [f"### 会员概况（观察日 {o['snapshot_date']}）",
              f"- 会员 {o['total_members']:,} 人，近 30 天活跃 {o['active_members_30d']:,} 人",
              f"- 预测未来 90 天收入 {_fmt_money(o['pred_revenue_next_90d'], cur)}，流失风险金额 {_fmt_money(o['total_value_at_risk'], cur)}",
              "", "| 阶段 | 人数 | 人数占比 | 收入占比 |", "|---|---|---|---|"]
    lines += [f"| {s['stage_name']} | {s['members']} | {s['member_share']:.0%} | {s['revenue_share']:.0%} |" for s in o["by_stage"]]
    example = store.members[store.members["stage"] == "at_risk"]["value_at_risk"].idxmax()
    lines += ["", f"可以试试问：“哪些高价值会员有流失风险？”、“会员 {example} 的情况”、“预测靠谱吗？”"]
    return {"answer": "\n".join(lines), "tool_calls": calls, "mode": "offline"}
