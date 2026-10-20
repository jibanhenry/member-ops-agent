# 会员运营 Agent（支付宝·智能体涌现奖 参赛原型）

面向门店/商家的会员运营助手：读取会员系统的消费记录，自动完成用户画像、生命周期划分、付费预测、流失预警，
并按商家自己的运营 Playbook 给出可执行、算得过账的运营动作。对应赛道「消费新体验 · 2.2 用户运营」。

## 快速开始

```bash
uv sync
uv run python -m member_ops.pipeline        # 数据 -> 模型 -> data/processed
uv run uvicorn app.server:app --port 8000   # 打开 http://localhost:8000
```

### 运营助手用哪个大模型（全部可免费运行）

数据分析和建模（画像、生命周期、付费预测、流失预警）全部在本地完成，不需要任何大模型。
只有右侧对话助手需要大模型，启动时按以下顺序自动选择，也可用 `LLM_BACKEND=ollama|claude|offline` 指定：

1. **本地模型（默认，免费）**：[Ollama](https://ollama.com) + `qwen3.5-9b-local`（即 `/Volumes/T8/SL/Chatweb/models/Qwen3.5-9B-Q4_K_M.gguf`）。
   数据不出本机。M3/16GB 上每个问题约 40 秒~2 分钟。可用 `OLLAMA_MODEL`、`OLLAMA_URL`、`OLLAMA_NUM_CTX` 覆盖。
2. **Claude**：设置 `ANTHROPIC_API_KEY` 时可选，分析质量更高，按量付费。
3. **离线规则引擎**：没有任何模型时兜底，看板和基础问答仍可用。

本地小模型容易"心算"投入产出，Agent 会拦截没有经过 `simulate_campaign` 测算的 ROI 数字并要求重算。

对话走流式接口 `POST /api/chat/stream`（NDJSON 事件流：`tool_start` / `tool_end` / `text` / `reset` / `done`），
界面上实时显示正在调用的工具和逐字生成的回答；`POST /api/chat` 保留为一次性返回的非流式接口。

## 演示数据

默认使用 **2018 年全国大学生数学建模竞赛 C 题"大型百货商场会员画像描绘"** 的公开附件：国内一家大型百货商场
（以高端美妆专柜为主）的会员系统数据。竞赛附件没有明确许可，仅用于非商业演示。

- 会员档案：卡号、出生日期、性别、入会时间（19.5 万人）
- 销售流水：卡号、时间、商品、数量、标价、实付金额、积分、专柜（189 万行，含非会员流水）
- 建模只用 **2016-04-01 至 2017-12-31** 的连续数据：2015-09~11 流水缺失，2016-01~03 绝大多数流水没有卡号；
  会员 vs 非会员对比只用 2016-04~2017-09（之后的流水只剩会员）
- 性别编码 0/1 的含义数据字典未给出，按买家中 0 占约 90%、以美妆为主推断 0=女、1=男

原始文件放在 `data/raw/dept_store/`：`项目数据1.xlsx`（会员信息）、`项目数据2.csv`（销售流水），
取自 GitHub 镜像 [kuluneko/portrait](https://github.com/kuluneko/portrait)（`项目数据2.rar` 解压得到 csv）。

备用数据集：设置 `MEMBER_DATASET=completejourney` 可切回美国超市会员数据（dunnhumby The Complete Journey，CC0），
同时把 `playbook/playbook_supermarket.yaml` 复制为 `playbook.yaml`。

商家自有数据：导出「会员 ID、订单号、消费时间、实付金额」四列即可，见 `member_ops/data.py::load_orders_csv`。

## 结构

| 路径 | 作用 |
|---|---|
| `member_ops/data.py` | 原始数据 -> 标准订单表 + 会员档案；各数据集的特征窗口与流失定义（DatasetConfig） |
| `member_ops/features.py` | 观察时点上的会员特征快照（RFM、节奏、趋势、优惠敏感度） |
| `member_ops/clv.py` | 付费预测：BG/NBD × Gamma-Gamma（scipy 实现） |
| `member_ops/churn.py` | 流失预警：时间切分训练的 GBDT + 可读流失原因 |
| `member_ops/segments.py` | RFM 八类、价值分层（历史 × 预测）、生命周期、策略匹配 |
| `member_ops/economics.py` | 算账：用户 ROI =（预测消费 × 实际毛利率 − 运营成本）÷ 运营成本，运营成本 = 固定成本 + 动作投入 + 权益；羊毛党逐人判断是否发券；各人群预算与整体 ROI |
| `member_ops/campaigns.py` | 活动与对照实验：按风险金额分层随机留对照组，结束后对比两组"实际 − 预测"算出额外消费、置信区间和增量 ROI；记录存于 `data/campaigns.json` |
| `member_ops/insights.py` | 付费预期跟踪（检查点预测 vs 实际、未达预期原因）、高价值画像 TGI 与历史对比、拉新来源质量 |
| `member_ops/actions.py` | 行动编排：个人补货节奏、按优惠敏感度选权益、成本上限、导购产能排队、大促档期，给每位会员排一个下一步动作 |
| `member_ops/pipeline.py` | 端到端管线与回测报告 |
| `member_ops/tools.py` | Agent 工具层（也供 Web API 复用） |
| `member_ops/agent.py` | 工具调用循环（Ollama 本地模型 / Claude）+ 离线规则引擎 |
| `playbook/playbook.yaml` | **运营规则**：生命周期阈值、价值分层、补货节奏、权益分组（高价值不降价、羊毛党）、算账口径与成本、付费预期跟踪、触达频控与导购产能、大促日历、阶段×价值 -> 策略与预算 |
| `playbook/methodology.md` | **运营方法论**：原样进入 Agent 系统提示词 |
| `app/` | FastAPI 服务与单页看板 |

改 `playbook/` 下两个文件即可把自己的会员运营思想注入产品，改完重跑管线。

## 当前回测结果（百货商场数据）

- 付费预测（2017-10-02 起 90 天）：购买次数 预测 16,973 / 实际 16,472；收入 预测 ¥7,701 万 / 实际 ¥7,683 万；会员价值排序相关 0.44
- 流失预警（2017-07-04 起 180 天）：AUC 0.78；风险最高 10% 会员实际流失 85%，整体 63%
- 会员 vs 非会员（2016-04~2017-09）：会员订单数是非会员的 97%，客单价高 8%，贡献 51% 的收入
