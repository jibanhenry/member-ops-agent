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
只有对话助手、简报改写和话术起草需要大模型。密钥放在项目根目录的 `.env`（参考 `.env.example`，已被 git 忽略），
启动时按以下顺序自动选择，也可用 `LLM_BACKEND=cloud|ollama|claude|offline` 指定：

1. **云端开源模型（配了密钥时默认）**：OpenAI 兼容接口，按 `LLM_PROVIDERS` 的顺序依次尝试，出错或额度用完自动换下一个。
   - 魔搭 API-Inference 的 `Qwen/Qwen3.8-Flash-Next`（`MODELSCOPE_TOKEN`）：每次调用扣 1 魔粒，实名并验证邮箱后每天约 250 魔粒，免费
   - DeepSeek `deepseek-flash`（`DEEPSEEK_API_KEY`）：备用，按量付费
   - 一个问题约 15~20 秒（含 1~3 次工具调用）
2. **本地模型（私有部署，数据不出本机）**：[Ollama](https://ollama.com) + `qwen3.5-9b-local`，`LLM_BACKEND=ollama`。
   M3/16GB 上每个问题约 40 秒~5 分钟。可用 `OLLAMA_MODEL`、`OLLAMA_URL`、`OLLAMA_NUM_CTX` 覆盖。
3. **Claude**：设置 `ANTHROPIC_API_KEY` 时可选，按量付费。
4. **离线规则引擎**：没有任何模型时兜底，看板和基础问答仍可用。

**隐私层**（`member_ops/privacy.py`）：数据发给任何云端模型之前，会员卡号换成会话内的临时代号（`M-0001`），
去掉精确年龄、性别、入会日期等回答运营问题用不到的个人字段；模型回答回来再还原成真实卡号显示。
`PRIVACY_MODE=strict` 时云端模型只能拿到汇总数据，拿不到单个会员明细。本地模型不经过这一层。

模型容易"心算"投入产出，Agent 会拦截没有经过 `simulate_campaign` / `get_budget_plan` 测算的带数字的 ROI，每个问题最多纠正一次。

对话走流式接口 `POST /api/chat/stream`（NDJSON 事件流：`tool_start` / `tool_end` / `text` / `reset` / `done`），
界面上实时显示正在调用的工具和逐字生成的回答；`POST /api/chat` 保留为一次性返回的非流式接口。

### 线上部署（魔搭 Docker 创空间，免费）

公开演示放在魔搭 Docker 创空间（免费 2 核 16G，国内可访问，监听 7860 端口）。会员数据不进代码仓库，
放在魔搭**私有数据集**里，容器启动时用只读令牌下载（`deploy/start.py`）。

1. `uv run python deploy/package.py`：生成 `dist/studio/`（要上传到创空间的代码，含 `Dockerfile` 和创空间卡片）
   和 `dist/data/`（要上传到私有数据集的处理后数据，约 16MB）
2. 在魔搭新建**私有数据集**，上传 `dist/data/` 里的全部文件
3. 新建创空间：SDK 选 Docker、资源选免费 CPU、设为公开；上传 `dist/studio/` 里的全部文件
4. 创空间"设置 → 环境变量"：`MODELSCOPE_TOKEN`（只读令牌）、`DATA_DATASET`（如 `用户名/数据集名`）、可选 `DEEPSEEK_API_KEY`
5. 点"上线"，在"查看日志"里确认看到 `Uvicorn running on http://0.0.0.0:7860`

**修改后怎么发布**（上传需要 `.env` 里的 `MODELSCOPE_WRITE_TOKEN`，即魔搭写权限令牌）：

| 改了什么 | 要做的 |
|---|---|
| 代码、页面、`methodology.md` | 本地跑通 → `uv run --with modelscope==1.40.3 python deploy/upload.py studio` → 创空间「空间管理 → 重新部署 → 确认部署」 |
| `playbook.yaml`、模型、数据 | 先 `uv run python -m member_ops.pipeline`（生命周期、策略等是管线算好存进数据的）→ `upload.py all` → 重新部署 |
| 环境变量（令牌、DeepSeek Key） | 创空间「部署设置 → 调整部署设置」修改后「确认并部署」；密文变量粘贴后确认框里出现圆点再保存，否则会存成空值 |

部署后在「查看日志」里确认：`环境变量 MODELSCOPE_TOKEN：已配置（39 个字符）`、`Uvicorn running on http://0.0.0.0:7860`。

**魔粒**：魔搭免费模型每调用一次扣 1 魔粒，一个问题约 2~4 魔粒；每天登录魔搭网站发放 250 魔粒（当天有效，北京时间零点重置），
网页部署本身用免费 CPU，不扣魔粒。模型返回额度不足时，助手当天改用规则引擎并明确提示"今天的智能分析额度已经用完"，
北京时间零点后自动恢复，不会每个问题都白等一次失败。

镜像默认 `DEMO_MODE=1`：访客新建的活动、修改的任务写到临时目录，每 `DEMO_RESET_HOURS`（默认 6）小时自动清空；
不暴露接口文档页；每个访客每分钟最多提问 `CHAT_PER_MINUTE`（默认 6）次，全站每天最多 `LLM_DAILY_LIMIT`（默认 80）次
调用大模型，超出后助手自动改用规则引擎。

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
| `member_ops/tasks.py` | 智能体工作台：每周简报、按专柜派发的导购任务、按会员事实起草话术与合规检查、导购回填结果；记录存于 `data/tasks.json` |
| `member_ops/alipay.py` | 支付宝开放平台适配：商家券活动、小程序订阅消息、会员卡更新的请求构造；未对接商家时只生成不发送，记录存于 `data/outbox.json` |
| `member_ops/insights.py` | 付费预期跟踪（检查点预测 vs 实际、未达预期原因）、高价值画像 TGI 与历史对比、拉新来源质量 |
| `member_ops/actions.py` | 行动编排：个人补货节奏、按优惠敏感度选权益、成本上限、导购产能排队、大促档期，给每位会员排一个下一步动作 |
| `member_ops/pipeline.py` | 端到端管线与回测报告 |
| `member_ops/tools.py` | Agent 工具层（也供 Web API 复用） |
| `member_ops/agent.py` | 工具调用循环：云端开源模型（魔搭 / DeepSeek，自动切换）、本地 Ollama、Claude，以及离线规则引擎 |
| `member_ops/privacy.py` | 隐私层：发给云端模型前卡号换代号、去掉非必要个人字段，回答回来再还原 |
| `member_ops/settings.py` / `limits.py` | 读取 `.env`、演示模式与状态目录；访客提问频率与每日模型额度保护 |
| `deploy/` + `Dockerfile` | 魔搭 Docker 创空间部署：打包脚本、容器启动脚本、创空间卡片 |
| `playbook/playbook.yaml` | **运营规则**：生命周期阈值、价值分层、补货节奏、权益分组（高价值不降价、羊毛党）、算账口径与成本、付费预期跟踪、触达频控与导购产能、大促日历、阶段×价值 -> 策略与预算 |
| `playbook/methodology.md` | **运营方法论**：原样进入 Agent 系统提示词 |
| `app/` | FastAPI 服务与单页看板 |

改 `playbook/` 下两个文件即可把自己的会员运营思想注入产品，改完重跑管线。

## 当前回测结果（百货商场数据）

- 付费预测（2017-10-02 起 90 天）：购买次数 预测 16,973 / 实际 16,472；收入 预测 ¥7,701 万 / 实际 ¥7,683 万；会员价值排序相关 0.44
- 流失预警（2017-07-04 起 180 天）：AUC 0.78；风险最高 10% 会员实际流失 85%，整体 63%
- 会员 vs 非会员（2016-04~2017-09）：会员订单数是非会员的 97%，客单价高 8%，贡献 51% 的收入
