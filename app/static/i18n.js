// 中英文切换：页面源码和接口数据都保持中文，切到英文时在显示层翻译。
// 做法：监听 DOM 变化，把文本节点和 placeholder / aria-label / title 属性翻成英文，原文留底，切回中文时原样恢复。
// 带 data-noi18n 的区域不翻（大模型的回答、用户输入、请求 JSON）。
(function () {
  "use strict";

  // ---------- 词典：整句或短语 ----------
  const D = {
    // 顶栏与分区
    "会员运营工作台": "Member Ops Workbench",
    "页面分区": "Page sections",
    "会员概览": "Overview",
    "行动计划": "Action plan",
    "预算与活动": "Budget & campaigns",
    "价值洞察": "Value insights",
    "演示数据": "Demo data",
    "语言": "Language",
    "2018 年全国大学生数学建模竞赛 C 题·大型百货商场会员数据": "CUMCM 2018 Problem C · Department-store member data",
    "竞赛公开附件，仅用于非商业演示": "Public competition attachment; non-commercial demo use only",

    // 会员概览
    "智能体本周简报": "Agent's weekly brief",
    "智能体根据本周数据整理，数字都来自看板的计算结果": "Compiled by the agent from this week's data; every number comes from the dashboard's calculations",
    "让智能体重写": "Rewrite with AI",
    "展开全文": "Expand",
    "收起": "Collapse",
    "智能体正在重写简报，可能需要一两分钟…": "The agent is rewriting the brief; this may take a minute or two…",
    "今天先做这几件事": "Start with these today",
    "按风险金额和时效排序": "Sorted by value at risk and urgency",
    "谁在贡献收入": "Who drives revenue",
    "各生命周期阶段的人数占比 vs 收入占比，点一行看该阶段名单": "Share of members vs. share of revenue by lifecycle stage. Click a row to see the member list",
    "人数": "Members",
    "收入": "Revenue",
    "风险集中在哪": "Where the risk sits",
    "圆越大人越多，颜色越深流失风险金额越高，点圆看策略和名单": "Bigger circle = more members; darker = more value at risk. Click a circle for strategy and member list",
    "风险金额 低": "Value at risk: low",
    "高": "high",
    "本周必做": "Must do this week",
    "进行中": "In progress",
    "新发现": "New finding",
    "自动执行中": "Running automatically",
    "大促前的自动挽留合并发送，确认权益与文案": "Automated win-backs before the promotion go out with the promo push; confirm incentives and copy",
    "近期实际消费明显少于预期": "Recent actual spend is well below expectation",
    "按个人购买节奏或品牌周期提醒": "Reminders timed by personal purchase rhythm or brand cycle",
    "去导购任务": "Go to associate tasks",
    "查看名单": "View list",
    "会员总数": "Total members",
    "未来 90 天预计收入": "Expected revenue, next 90 days",
    "按会员购买节奏预测": "Forecast from member purchase rhythm",
    "各月收入占全年，深色为全年最旺的两个月": "Monthly share of annual revenue; dark = the two peak months",
    "流失风险金额": "Value at risk",
    "会员运营回报 →": "Return on member ops →",
    "深色为投入，浅色为预测毛利": "Dark = cost, light = predicted gross profit",
    "人数占比": "Share of members",
    "收入占比": "Share of revenue",
    "点击查看名单": "Click to view the list",
    "读图：": "Reading the chart: ",
    "预测靠谱吗": "Can the forecasts be trusted?",
    "用历史数据回看当时的预测": "Back-testing past forecasts against what actually happened",
    "准": "Accurate",
    "，实际": ", actual",
    "判定风险最高的 10% 会员里": "Of the 10% of members rated highest-risk,",
    "看板数据暂时没加载出来，请稍后刷新页面重试。": "Dashboard data failed to load. Please refresh the page and try again.",

    // 生命周期与价值分层
    "新客期": "New",
    "成长期": "Growing",
    "成熟期": "Mature",
    "衰退预警": "At risk",
    "沉睡": "Dormant",
    "流失": "Lost",
    "单次客": "One-time",
    "高价值": "High value",
    "中价值": "Mid value",
    "低价值": "Low value",
    "高价值·不降价": "High value · no price cuts",
    "羊毛党": "Deal hunters",
    "价格敏感": "Price-sensitive",
    "非价格敏感": "Not price-sensitive",
    "价值下滑": "Value declining",
    "潜力上升": "Rising potential",
    "重要价值客户": "Champions",
    "重要保持客户": "Keep loyal",
    "重要发展客户": "Develop",
    "重要挽留客户": "Win back",

    // 行动计划
    "自动执行": "Automated",
    "暂不打扰": "Do not disturb",
    "本周人工回访": "Personal follow-up this week",
    "排队等人工回访": "Queued for personal follow-up",
    "未达预期定向触达": "Below-expectation targeted outreach",
    "补货提醒": "Replenishment reminder",
    "并入大促推送": "Merged into promo push",
    "自动触达": "Automated outreach",
    "仅大促触达": "Promo-only outreach",
    "对照组·不触达": "Control group · no outreach",
    "名单 ›": "List ›",
    "暂无": "None",
    "导购任务": "Associate tasks",
    "本周人工回访和消费不及预期的会员，按会员常去的专柜派给导购；点一行，由智能体起草话术，导购确认后才生成支付宝请求":
      "Members due a personal follow-up this week or spending below expectation, assigned to sales associates by the member's usual counter. Click a row for an AI-drafted script; Alipay requests are generated only after the associate confirms",
    "按专柜筛选": "Filter by counter",
    "按状态筛选": "Filter by status",
    "全部专柜": "All counters",
    "全部状态": "All statuses",
    "导购排期": "Associate schedule",
    "大促节奏": "Promotion calendar",
    "转自动触达": "Switched to automated",
    "年货节": "New Year Sale",
    "三八女神节": "Women's Day",
    "年中大促": "Mid-year Sale",
    "本周任务": "Tasks this week",
    "待处理": "Pending",
    "话术已确认": "Script confirmed",
    "已生成支付宝请求": "Alipay request generated",
    "已联系": "Contacted",
    "话术已确认 / 已生成请求": "Script confirmed / request generated",
    "已到柜": "Visited counter",
    "有意向": "Interested",
    "未接通": "No answer",
    "明确拒绝": "Declined",
    "其他": "Other",
    "会员": "Members",
    "专柜": "Counter",
    "任务": "Task",
    "原因": "Reason",
    "风险金额": "Value at risk",
    "状态": "Status",
    "人工回访": "Personal follow-up",
    "未达预期跟进": "Below-expectation follow-up",
    "不降价": "No price cuts",
    "没有符合条件的任务": "No matching tasks",
    "再加载 20 条": "Load 20 more",

    // 导购任务弹窗
    "加载中…": "Loading…",
    "触达渠道": "Channel",
    "导购企业微信": "Associate's WeCom",
    "电话（导购口述要点）": "Phone (talking points)",
    "支付宝小程序订阅消息": "Alipay mini-program subscription message",
    "智能体起草": "Draft with AI",
    "用模板": "Use template",
    "话术": "Script",
    "点「智能体起草」生成话术，导购可以直接修改": "Click \"Draft with AI\" to generate a script; the associate can edit it directly",
    "导购确认话术": "Confirm script",
    "生成支付宝请求": "Generate Alipay request",
    "查看会员详情 ›": "View member details ›",
    "联系结果（由导购回填）": "Contact outcome (entered by the associate)",
    "联系结果": "Contact outcome",
    "选择结果…": "Choose an outcome…",
    "备注（选填）": "Note (optional)",
    "备注": "Note",
    "记录结果": "Save outcome",
    "智能体起草中，可能需要一两分钟…": "Drafting with AI; this may take a minute or two…",
    "禁止价格减免": "No price cuts allowed",
    "单人成本上限": "Per-member cost cap",
    "智能体起草时只用到以上事实，拿不到会员的生日、卡号和消费明细。": "The AI draft uses only the facts above; it has no access to the member's birthday, card number or purchase details.",
    "话术已确认，可以生成支付宝请求，或由导购在企业微信、电话里使用。": "Script confirmed. You can generate the Alipay request, or the associate can use it on WeCom or by phone.",
    "请先选择联系结果。": "Please choose a contact outcome first.",
    "结果已记录。系统只记录导购回填的结果，不会自动生成结果。": "Outcome saved. The system only records what the associate enters; it never makes up outcomes.",
    "高价值会员、不发券的羊毛党不出现价格减免": "No price cuts for high-value members or deal hunters excluded from coupons",
    "承诺的金额不超过单人成本上限": "Promised amount stays within the per-member cost cap",
    "不向会员透露天数、分层、流失判断等分析数据": "No analytics revealed to the member (day counts, tiers, churn judgments)",
    "不包含手机号、支付宝账号、证件号等个人信息": "No personal data (phone number, Alipay account, ID number)",
    "不为空": "Not empty",
    "疑似个人信息": "possible personal data",
    "模板": "Template",
    "离线规则模式": "Offline rule mode",
    "没有可用的大模型": "no LLM available",

    // 预算与活动
    "钱花在哪、回报多少": "Where the money goes and what it returns",
    "用户 ROI =（预测一年消费 × 实际毛利率 − 运营成本）÷ 运营成本。运营成本 = 固定成本 + 动作投入 + 权益。":
      "Member ROI = (predicted 1-year spend × actual gross margin − operating cost) ÷ operating cost. Operating cost = fixed cost + action cost + incentives.",
    "每个圆是一类人群：越靠右人均投入越多，越靠上回报越高，圆越大人越多": "Each circle is a segment: further right = higher cost per member, higher up = higher return, bigger = more members",
    "回报最低的 5 类人群": "5 lowest-return segments",
    "优先检查这些人群的投入，点一行看策略和名单": "Review spending on these segments first. Click a row for strategy and member list",
    "全部人群 ›": "All segments ›",
    "只看最低 5 类": "Show lowest 5 only",
    "一年运营成本": "1-year operating cost",
    "会员预测毛利": "Predicted member gross profit",
    "整体回报": "Overall return",
    "羊毛党不发券": "Deal hunters without coupons",
    "回报为负的会员": "Members with negative return",
    "人均一年运营成本": "1-year operating cost per member",
    "预测回报（倍）": "Predicted return (×)",
    "预测回报": "Predicted return",
    "预测毛利": "Predicted gross profit",
    "待验证": "Not yet measured",
    "仅供参考": "Indicative only",
    "人群": "Segment",
    "活动实测": "Measured in campaigns",
    "建议": "Advice",
    "保持": "Keep as is",
    "仅大促低成本触达": "Low-cost promo-only outreach",
    "仅大促低成本触达（规则）": "Low-cost promo-only outreach",
    "削减投入，只保留最低成本触达": "Cut spending; keep only the lowest-cost outreach",
    "压缩权益成本": "Reduce incentive cost",
    "活动效果验证": "Campaign measurement",
    "随机留对照组，活动结束后对比两组的实际消费": "Hold out a random control group, then compare the two groups' actual spend after the campaign",
    "看一个示例": "See an example",
    "＋ 新建活动": "+ New campaign",
    "活动名称": "Campaign name",
    "目标人群": "Target segment",
    "权益分组": "Incentive group",
    "动作": "Action",
    "权益类型": "Incentive type",
    "单人权益（元）": "Incentive per member (¥)",
    "预计核销率": "Expected redemption rate",
    "对照组比例": "Control group share",
    "观察天数": "Observation days",
    "全部": "All",
    "赠品/小样（按成本价）": "Gift / sample (at cost)",
    "满减折扣（按面额）": "Discount (at face value)",
    "体验服务": "Experience service",
    "取消": "Cancel",
    "登记并分组": "Register & split groups",
    "补货提醒 + 积分兑换小样": "Replenishment reminder + samples for points",
    "① 选人群和权益": "① Pick a segment and incentive",
    "系统按风险金额分层，随机留出一部分会员作对照组": "The system stratifies by value at risk and randomly holds out some members as a control group",
    "② 活动执行": "② Run the campaign",
    "对照组在活动期间不触达，其他进行中的活动会自动避开": "The control group is not contacted during the campaign; members in other running campaigns are excluded automatically",
    "③ 对比结果": "③ Compare results",
    "活动组比对照组多买了多少、这笔投入值不值": "How much more the treated group bought than the control group, and whether the spend paid off",
    "活动": "Campaign",
    "周期": "Period",
    "活动组 / 对照组": "Treated / control",
    "每人额外消费": "Incremental spend per member",
    "实测回报": "Measured return",
    "结论": "Verdict",
    "结论：": "Verdict: ",
    "示例": "Example",
    "未评估": "Not evaluated",
    "评估": "Evaluate",
    "删除": "Delete",
    "删除这个活动？对照组会员会恢复正常触达。": "Delete this campaign? Control-group members will go back to normal outreach.",
    "活动组": "Treated",
    "对照组": "Control",
    "结果可信": "Reliable result",
    "差异可能只是偶然，暂不作为决策依据": "The difference may be chance; don't base decisions on it yet",
    "还没有评估：活动结束、数据更新后点「评估」。": "Not evaluated yet: click \"Evaluate\" once the campaign has ended and the data is updated.",
    "为活动组生成支付宝请求": "Generate Alipay requests for the treated group",
    "只生成、不发送；对照组不发": "Generated only, never sent; nothing for the control group",
    "有效：额外毛利覆盖成本，可以继续或加码": "Effective: incremental gross profit covers the cost; continue or scale up",
    "带来了额外消费，但不够覆盖成本：压缩权益成本": "Drove incremental spend, but not enough to cover the cost: reduce incentive cost",
    "没有测出可靠的额外消费（区间包含 0）：不要据此加码，可扩大对照组或延长周期再测": "No reliable incremental spend detected (the interval includes 0): don't scale up on this; enlarge the control group or extend the window and test again",
    "回放演示：高价值衰退预警导购回访": "Replay demo: associate follow-up for high-value at-risk members",
    "导购回访 + 到柜护理体验": "Associate follow-up + in-store care experience",
    "回放演示：这个活动没有真实执行，两组都没被触达，组间差距应接近 0，用来检验评估方法不会凭空算出效果。人群是按今天的阶段圈的，两组的'实际 − 预测'都偏低，但组间对比仍然公平。回放结果不计入预算表的实测证据":
      "Replay demo: this campaign was never actually run and neither group was contacted, so the gap between groups should be close to 0. It checks that the evaluation method doesn't conjure an effect out of nothing. Members were selected by today's stage, so 'actual − predicted' is low for both groups, but the comparison between groups is still fair. Replay results don't count as measured evidence in the budget table",
    "尚无数据": "No data yet",
    "已结束": "Ended",
    "历史累计消费": "Historical spend",
    "流失概率": "Churn probability",
    "支付宝对接": "Alipay integration",
    "运营动作对应的支付宝开放接口。当前没有对接真实商家，请求只生成、不发送，不会实际发券或发消息":
      "Alipay Open APIs matching each ops action. No real merchant is connected: requests are generated but never sent, so no coupons or messages actually go out",
    "未对接商家，未发送": "No merchant connected · not sent",
    "已生成的请求": "Generated requests",
    "运营动作": "Ops action",
    "支付宝能力": "Alipay capability",
    "接口": "API",
    "前提": "Prerequisite",
    "时间": "Time",
    "来源": "Source",
    "补货提醒、未达预期提醒、活动通知": "Replenishment reminders, below-expectation reminders, campaign notices",
    "小程序订阅消息": "Mini-program subscription message",
    "会员在商家小程序订阅过消息模板；会员授权得到支付宝 user_id": "Member has subscribed to the message template in the merchant mini-program; Alipay user_id obtained with the member's consent",
    "让利型权益（只给价格敏感会员、算得过账的羊毛党）": "Price-cut incentives (only for price-sensitive members and deal hunters who still pay off)",
    "商家券 2.0": "Merchant Coupon 2.0",
    "商家开通商家券；券在小程序领取、支付时核销": "Merchant has enabled merchant coupons; coupons are claimed in the mini-program and redeemed at payment",
    "积分礼遇、等级保级（高价值会员的非价格权益）": "Points perks and tier protection (non-price incentives for high-value members)",
    "商家会员卡": "Merchant member card",
    "会员领取过商家会员卡，拿到支付宝卡号": "Member has claimed the merchant member card, giving an Alipay card number",
    "活动效果回收": "Collecting campaign results",
    "商家券查询": "Merchant coupon query",
    "按会员查询领券和核销情况，回填到对照实验评估": "Query coupon claims and redemptions per member and feed them back into the controlled-experiment evaluation",
    "创建商家券活动": "Create merchant coupon campaign",
    "会员卡更新": "Member card update",
    "还没有生成请求。在「行动计划 › 导购任务」里确认话术后，或在活动详情里，可以生成对应的支付宝接口请求。":
      "No requests yet. Confirm a script under \"Action plan › Associate tasks\", or open a campaign's details, to generate the matching Alipay API requests.",

    // 价值洞察
    "高价值会员集中在哪些人里": "Where high-value members concentrate",
    "集中度 = 该特征在高价值会员中的占比 ÷ 在全体会员中的占比 × 100。大于 120 说明高价值会员明显集中在这类人里。":
      "Concentration index = share of the trait among high-value members ÷ share among all members × 100. Above 120 means high-value members clearly concentrate in this group.",
    "集中度，100 表示与整体持平": "Concentration index; 100 = same as overall",
    "100 = 与整体持平": "100 = same as overall",
    "暂无明显集中的特征": "No clearly concentrated traits",
    "会员 vs 非会员": "Members vs. non-members",
    "非会员": "Non-members",
    "订单数": "Orders",
    "客单价": "AOV",
    "件数/单": "Items/order",
    "拉新来源质量": "Acquisition quality",
    "拉新维度": "Acquisition dimension",
    "按首单金额": "By first-order amount",
    "按首单品牌": "By first-order brand",
    "暂无数据": "No data",
    "高潜新会员": "High-potential new members",
    "建议放进「新客期」的首次体验闭环里重点培育": "Nurture them through the first-experience loop of the \"New\" stage",
    "暂无高潜新会员": "No high-potential new members",
    "年龄段": "Age",
    "性别": "Gender",
    "入会年限": "Tenure",
    "主要消费品牌": "Main brand",
    "首单品牌": "First brand",
    "首单金额段": "First order",
    "收入段": "Income band",
    "家庭结构": "Household",
    "主要消费品类": "Main category",
    "男": "Male",
    "女": "Female",
    "首单<1千": "< ¥1K",
    "首单1千-3千": "¥1K–3K",
    "首单3千-1万": "¥3K–10K",
    "首单≥1万": "≥ ¥10K",

    // 品牌 / 专柜
    "雅诗兰黛ESTEE LAUDER": "Estée Lauder",
    "阿玛尼GIORGIO ARMANI": "Giorgio Armani",
    "科颜氏(Kiehl's)": "Kiehl's",
    "莱珀妮(La Prairie)": "La Prairie",
    "OHUI/后": "OHUI / Whoo",
    "万宝龙柜(综合转男装)": "Montblanc",
    "莱尔斯丹（新）": "Le Saunda",
    "谢瑞麟柜": "TSL Jewellery",
    "周大福柜": "Chow Tai Fook",
    "六福柜": "Lukfook Jewellery",
    "瑞士钟表柜": "Swiss Watches",
    "英皇珠宝": "Emperor Jewellery",
    "欧莱雅柜": "L'Oréal",
    "欧舒丹柜": "L'Occitane",
    "兰芝柜": "Laneige",
    "施华洛世奇柜": "Swarovski",
    "双立人柜": "Zwilling",
    "未知专柜": "Unknown counter",
    "门店": "Store",

    // 人群侧栏
    "导出名单": "Export list",
    "为这群人发起活动": "Start a campaign for this group",
    "关闭": "Close",
    "名单": "List",
    "策略": "Strategy",
    "算账": "Economics",
    "按风险金额排序": "Sorted by value at risk",
    "距上次消费": "Since last purchase",
    "消费次数": "Purchases",
    "累计消费": "Total spend",
    "主要信号": "Key signals",
    "下一步": "Next step",
    "这类人群暂时没有会员": "No members in this segment yet",
    "导购人工（按风险金额排队）": "Associate, in person (queued by value at risk)",
    "只在大促触达": "Promo-only outreach",
    "权益": "Incentive",
    "考核": "KPI",
    "频控": "Guardrail",
    "下一步安排": "Next steps",
    "价格敏感会员": "Price-sensitive members",
    "回报为负": "Negative return",
    "消费不及预期的会员": "Members spending below expectation",
    "会员结构": "Member structure",

    // 会员详情
    "流失信号": "Churn signals",
    "是": "Yes",
    "否": "No",
    "画像": "Profile",
    "消费行为": "Purchase behaviour",
    "优惠与积分": "Discounts & points",
    "付费预期": "Spend expectation",
    "运营编排": "Ops plan",
    "预测": "Predictions",
    "生命周期": "Lifecycle stage",
    "价值分层": "Value tier",
    "价值变化": "Value change",
    "年龄": "Age",
    "入会时间": "Joined",
    "购买过的品牌数": "Brands purchased",
    "首单金额": "First-order amount",
    "自有品牌占比": "Private-label share",
    "常去门店数": "Stores visited",
    "首次消费": "First purchase",
    "最近消费": "Latest purchase",
    "距今天数": "Days since",
    "累计消费次数": "Total purchases",
    "累计消费金额": "Total spend",
    "平均消费间隔天数": "Avg days between purchases",
    "优惠占原价比例": "Discount as share of list price",
    "用券订单占比": "Share of coupon orders",
    "累计积分": "Points earned",
    "收到营销活动数": "Campaigns received",
    "核销优惠券数": "Coupons redeemed",
    "打折订单占比": "Share of discounted orders",
    "检查点以来预期消费次数": "Expected purchases since checkpoint",
    "检查点以来实际消费次数": "Actual purchases since checkpoint",
    "检查点以来预期消费金额": "Expected spend since checkpoint",
    "检查点以来实际消费金额": "Actual spend since checkpoint",
    "未达预期": "Below expectation",
    "未达预期原因": "Reason below expectation",
    "下一步动作": "Next action",
    "动作日期": "Action date",
    "触达方式": "Outreach mode",
    "补货周期天数": "Replenishment cycle (days)",
    "周期依据": "Cycle basis",
    "补货提醒日": "Replenishment reminder date",
    "补货状态": "Replenishment status",
    "固定成本": "Fixed cost",
    "动作投入": "Action cost",
    "权益成本": "Incentive cost",
    "回报": "Return",
    "羊毛党发券决定": "Deal-hunter coupon decision",
    "仍活跃概率": "Probability still active",
    "个人节奏": "Personal rhythm",
    "品牌周期": "Brand cycle",
    "整体中位": "Overall median",
    "未到": "Not yet due",
    "补货窗口": "In replenishment window",
    "已过补货期": "Past replenishment window",
    "不发券（毛利抵不过发券成本）": "No coupon (gross profit can't cover coupon cost)",
    "可发券（毛利覆盖成本）": "Coupon OK (gross profit covers the cost)",
    "导购人工": "Associate, in person",
    "暂不打扰，到补货窗口再提醒": "Do not disturb; remind at the replenishment window",
    "仅年度大促触达": "Annual-promo outreach only",
    "原因待回访": "reason to be confirmed on follow-up",
    "（人工产能不足，转自动）": " (associate capacity full; switched to automated)",

    // 流失 / 未达预期信号
    "只买过 1 次，还没形成复购习惯": "Bought only once; no repeat habit yet",
    "所有消费都集中在同一天，还没形成回店习惯": "All purchases on a single day; no return-visit habit yet",
    "最近只在有折扣时才消费": "Recently buys only on discount",
    "检查点后没有到柜": "no counter visit since the checkpoint",
    "沉默超过平时间隔": "silent longer than their usual gap",
    "购买品牌变少": "buying fewer brands",
    "客单下降": "smaller basket size",
    "只在打折时才来": "only buys on discount",

    // 运营助手
    "问问运营助手": "Ask the ops assistant",
    "运营助手": "Ops assistant",
    "收起助手": "Collapse assistant",
    "问问你的会员…": "Ask about your members…",
    "输入问题": "Type a question",
    "发送": "Send",
    "正在": "",
    "未能": "Failed: ",
    "正在重新核对数字…": "Re-checking the numbers…",
    "连接中断": "connection lost",
    "你好，我是你的会员运营助手，可以帮你看会员结构、找流失风险、出运营方案并算账。回答一般需要一两分钟，期间可以收起我继续看板，答好了按钮上会有提醒。":
      "Hi, I'm your member ops assistant. I can walk you through member structure, find churn risk, and draft ops plans with the numbers worked out. An answer usually takes a minute or two; feel free to collapse me and keep using the dashboard, and the button will show a dot when I'm done.",
    "给我本周简报": "Give me this week's brief",
    "会员整体情况怎么样？": "How are our members doing overall?",
    "哪些高价值会员有流失风险？": "Which high-value members are at risk of churning?",
    "这周导购应该先联系谁？": "Who should associates contact first this week?",
    "帮 LANCOME 专柜的第一位任务会员写一条话术": "Write a script for the first task member at the LANCOME counter",
    "年货节前应该怎么安排触达？": "How should we schedule outreach before the New Year Sale?",
    "羊毛党要不要发券？": "Should deal hunters get coupons?",
    "给衰退预警的高价值会员设计一个挽留方案并算算账": "Design a retention plan for high-value at-risk members and work out the numbers",
    "活动有没有带来额外消费？": "Did the campaigns drive incremental spend?",
    "高价值会员是什么样的，拉新往哪倾斜？": "What do high-value members look like, and where should acquisition lean?",
    "会员和非会员差在哪？": "How do members differ from non-members?",
    "查看会员整体概况": "Viewing the member overview",
    "按维度拆分会员": "Breaking members down by dimension",
    "圈选会员名单": "Selecting a member list",
    "读取会员画像": "Reading the member profile",
    "查询运营策略": "Looking up the ops strategy",
    "测算活动投入产出": "Estimating campaign cost and return",
    "查看预算与 ROI": "Viewing budget and ROI",
    "查看高价值画像与拉新来源": "Viewing the high-value profile and acquisition sources",
    "登记活动并留对照组": "Registering a campaign with a control group",
    "查看活动与对照实验": "Viewing campaigns and controlled experiments",
    "评估活动增量效果": "Evaluating incremental campaign impact",
    "生成本周简报": "Generating this week's brief",
    "查看导购任务": "Viewing associate tasks",
    "准备话术素材": "Preparing script material",
    "查看本周行动计划": "Viewing this week's action plan",
    "核对预测准确度": "Checking forecast accuracy",

    // 简报（模板）
    "钱花得怎样": "How the money is working",
    "需要你决定的": "Decisions for you",
    "未配置档期": "none configured",
    "还没有登记过活动。用户 ROI 只说明会员值不值得花钱，建议本周挑一个人群上线活动并留对照组，验证钱有没有带来额外消费。":
      "No campaigns registered yet. Member ROI only says whether a member is worth the spend; pick one segment this week, launch a campaign with a control group, and verify whether the money drives incremental spend.",

    // 运营策略（Playbook）
    "入会 90 天首次体验闭环": "90-day first-experience loop",
    "单次客第二单激活": "Second-order activation for one-time buyers",
    "核心会员维系": "Core member retention",
    "沉睡会员批量唤醒": "Bulk reactivation of dormant members",
    "流失会员低成本维系": "Low-cost upkeep for lost members",
    "补货节奏培养": "Building a replenishment rhythm",
    "衰退预警自动唤醒": "Automated reactivation for at-risk members",
    "跨品牌连带推荐": "Cross-brand recommendations",
    "首单大客二次到柜": "Second visit for big first-order buyers",
    "高价值沉睡会员召回": "High-value dormant win-back",
    "高价值衰退预警挽留": "High-value at-risk retention",
    "高潜会员升级": "High-potential member upgrade",
    "14 天未响应再推一次新品或套装内容": "No response after 14 days: push new-product or set content once more",
    "180 天内消费次数、跨品牌购买数": "Purchases within 180 days; number of brands purchased",
    "30 天内回柜率": "Return-to-counter rate within 30 days",
    "30 天内最多触达 2 次；仍无响应的转入大促节点统一触达": "At most 2 touches in 30 days; non-responders move to promo-only outreach",
    "30 天未响应：推送一次首单品牌的新品或套装": "No response after 30 days: push a new product or set from the first-order brand once",
    "60 天内二次到柜率": "Second-visit rate within 60 days",
    "60 天内回柜率、挽留 ROI": "Return-to-counter rate within 60 days; retention ROI",
    "90 天内二次到柜率": "Second-visit rate within 90 days",
    "90 天内最多主动触达 4 次；已复购即转入成长期节奏": "At most 4 proactive touches in 90 days; once they repurchase, switch to the Growing-stage rhythm",
    "90 天召回率": "90-day win-back rate",
    "不再重投入，保留重新激活的可能": "No more heavy investment; keep the door open for reactivation",
    "不发大额通用券，避免利润侵蚀": "No large general-purpose coupons, to avoid eroding profit",
    "不对流失会员做高成本投放": "No high-cost spend on lost members",
    "不用大额折扣拉升，避免养成只在大促时购买": "No big discounts to pump sales, to avoid training promo-only buying",
    "专属导购 48 小时内电话或企业微信回访，了解是否转去其他渠道": "Dedicated associate follows up by phone or WeCom within 48 hours to learn whether they've moved to another channel",
    "人工优先于群发；同一会员一个周期内只做一次大额挽留": "Personal contact before mass messaging; only one big retention offer per member per cycle",
    "人工回访只做一次；30 天内最多触达 2 次": "One personal follow-up only; at most 2 touches in 30 days",
    "仅在年度大促时触达": "Outreach only during annual promotions",
    "会员等级/积分加速，突出身份感": "Faster tier/points progress to emphasise status",
    "低价值人群控制成本，优先用积分和内容；临近大促的并入大促推送": "Control cost for low-value members; favour points and content; merge into the promo push when a promo is near",
    "低成本拉回消费节奏": "Bring them back into a purchase rhythm at low cost",
    "入会 90 天内二次购买率": "Second-purchase rate within 90 days of joining",
    "入会当天：专属导购（美妆专柜为美容顾问）添加企业微信，发送积分规则和会员权益说明": "Joining day: dedicated associate (beauty advisor at beauty counters) adds the member on WeCom and sends points rules and member benefits",
    "入会第 60 天仍未复购：邀请参加专柜新品体验或妆容课": "No repurchase by day 60: invite to a new-product trial or make-up class at the counter",
    "分析流失原因，反哺商品和服务改进": "Analyse why they left and feed it back into product and service improvements",
    "到柜体验、同系列小样": "In-store experience; samples from the same line",
    "发放\"老会员回归礼\"，邀请到柜体验": "Send a \"welcome-back gift\" and invite them to the counter",
    "发送补货提醒和积分余额/到期提醒": "Send replenishment reminders and points balance/expiry reminders",
    "只给有门槛、算得过账的权益；发券回不了本就不发，只做内容和积分提醒": "Only incentives with a threshold that pay off; no coupon if it can't pay back, just content and points reminders",
    "召回失败的不再追加投入，转入低频内容触达": "No further spend on failed win-backs; switch to low-frequency content",
    "同一会员每月最多 2 次营销触达；不在补货窗口内不打扰": "At most 2 marketing touches per member per month; no contact outside the replenishment window",
    "告知其常买品牌的新品或专柜升级": "Tell them about new products or counter upgrades from their usual brand",
    "告知积分余额和可兑换的小样": "Tell them their points balance and which samples they can redeem",
    "回归礼 + 到柜满减": "Welcome-back gift + in-store spend-and-save",
    "回归礼（体验或小样礼盒）": "Welcome-back gift (experience or sample box)",
    "回访了解离开原因，记录到会员档案": "Follow up to learn why they left and record it in the member file",
    "在真正流失前拦截，这是投入产出比最高的人群": "Catch them before they really churn; this is the segment with the best return on spend",
    "基于常买品牌推荐关联品牌或品类（护肤→彩妆、香水、个护）": "Recommend related brands or categories based on their usual brand (skincare → make-up, fragrance, personal care)",
    "复购间隔是否缩短": "Whether the repurchase gap shortens",
    "定向礼遇 + 到柜满赠": "Targeted perks + gift with in-store purchase",
    "定向礼遇（体验、护理、限量品预留）": "Targeted perks (experiences, treatments, limited-edition reservations)",
    "对打开或点击的会员再做二次精准触达": "Follow up precisely with members who opened or clicked",
    "导购按首单品牌回访使用感受，确认是否适合肤质，必要时邀请到柜调整方案": "Associate follows up on how the first-order brand is working, checks it suits their skin, and invites them in to adjust the routine if needed",
    "小样、体验服务，不发折扣券": "Samples and experience services; no discount coupons",
    "小样、积分兑换": "Samples; points redemption",
    "年度留存率、年消费额、跨品牌数": "Annual retention, annual spend, number of brands",
    "店庆、双十一、年货节等大促节点统一推送一次": "One push at major promos (store anniversary, Double 11, New Year Sale)",
    "店庆、节日营销时优先触达": "Prioritise them in anniversary and holiday marketing",
    "指定专属导购，建立一对一联系": "Assign a dedicated associate and build a one-to-one relationship",
    "按会员自己的消费间隔（不足 3 次用品牌周期）在周期 75% 时提醒补货": "Remind at 75% of the member's own purchase interval (brand cycle if fewer than 3 purchases)",
    "按首单品牌的补货周期发送补货提醒和正确用法内容": "Send replenishment reminders and how-to content on the first-order brand's cycle",
    "按首单品牌的补货周期，在周期 75% 时提醒补货": "Remind at 75% of the first-order brand's replenishment cycle",
    "按首购品牌的补货周期，在周期 75% 时提醒补货，附同系列小样": "Remind at 75% of the first-purchase brand's replenishment cycle, with samples from the same line",
    "控制频率，避免被投诉为骚扰": "Control frequency to avoid harassment complaints",
    "推荐与已购品牌互补的品类（如护肤客推荐彩妆、香水）": "Recommend categories that complement brands already bought (e.g. make-up or fragrance for skincare buyers)",
    "推荐常用单品的套装或大容量装": "Recommend sets or larger sizes of their regular products",
    "推荐要与历史偏好相关，避免无差别群发": "Recommendations must match past preferences; no indiscriminate blasts",
    "提高连带率和购买品牌数；只买一个品牌的会员是\"专柜的客人\"，多品牌才是\"商场的客人\"": "Raise attach rate and brands purchased; a one-brand member is \"the counter's customer\", a multi-brand one is \"the store's customer\"",
    "新品优先试用、限量品预留": "Early access to new products; limited-edition reservations",
    "无或极低": "None or minimal",
    "有投诉或不满的先解决问题再谈礼遇": "If there's a complaint, fix the problem before offering perks",
    "流失原因归因覆盖率": "Coverage of churn-reason attribution",
    "淡季（2~3 月）邀请参加商场 VIP 活动，收集反馈；淡季导购有空，也避开大促让利": "In the off-season (Feb–Mar), invite to store VIP events and collect feedback; associates have time then, and it avoids promo price cuts",
    "满额赠小样": "Samples with qualifying spend",
    "生日、入会纪念日由专属导购送祝福和礼遇": "Dedicated associate sends greetings and perks on birthdays and membership anniversaries",
    "用新鲜感和专属感召回": "Win them back with novelty and exclusivity",
    "积分兑换": "Points redemption",
    "积分兑换、通用满减": "Points redemption; general spend-and-save",
    "积分兑换小样": "Samples for points",
    "积分加倍、满赠": "Double points; gift with purchase",
    "积分加倍日、满额赠小样": "Double-points days; samples with qualifying spend",
    "第二单满减，限 30 天内使用": "Spend-and-save on the second order, valid 30 days",
    "第二单满赠或积分加倍，限 30 天内使用": "Gift with purchase or double points on the second order, valid 30 days",
    "第二单满赠，30 天内有效": "Gift with purchase on the second order, valid 30 days",
    "筛出仍有意愿的会员": "Identify members who are still interested",
    "维持忠诚度和钱包份额，不打扰": "Maintain loyalty and share of wallet without disturbing them",
    "触达响应率、召回率": "Outreach response rate; win-back rate",
    "让会员形成按周期回柜补货的习惯": "Build the habit of returning to the counter to replenish on cycle",
    "让新会员完成第二次到柜，美妆的第二次购买通常是补货或同系列加购": "Get new members to a second visit; in beauty, the second purchase is usually a refill or an add-on from the same line",
    "跨品牌满减、积分兑换": "Cross-brand spend-and-save; points redemption",
    "跨品牌满额赠、积分兑换": "Cross-brand gift with purchase; points redemption",
    "这批人从没形成过习惯，目标不是\"挽留\"而是促成第二单": "These members never formed a habit; the goal is a second order, not \"retention\"",
    "连带率、购买品牌数、客单价": "Attach rate, brands purchased, AOV",
    "邀请到柜做一次新品体验（美妆可安排免费护理）": "Invite them in for a new-product experience (a free treatment for beauty)",
    "邀请参加品牌 VIP 沙龙、新品首发": "Invite to brand VIP salons and product launches",
    "邀请参加首单品牌的新品体验或妆容课": "Invite to a new-product trial or make-up class from the first-order brand",
    "非金钱权益优先（身份、体验、优先权）": "Non-monetary perks first (status, experiences, priority access)",
    "首几单就买得多的会员，尽快培养成多品牌、稳定复购的核心会员": "Members who spend big in their first few orders: grow them quickly into multi-brand, steadily repurchasing core members",
    "首单就买得多但只来过一次，是最值得争取第二单的人": "Spent big on the first order but came only once: the people most worth winning a second order from",
    "首购后第 7 天：回访使用感受，提醒正确用法，避免因用法不当放弃": "Day 7 after first purchase: check how it's going and remind them of correct usage so they don't give up",
  };

  // 只在整段文本完全相同时翻译，不做短语替换（太短或容易误伤）
  const EXACT_ONLY = new Set(["高", "准", "是", "否", "男", "女", "正在", "未能", "，实际", "回报", "预测", "建议", "活动", "任务", "原因", "状态",
    "动作", "策略", "名单", "全部", "其他", "权益", "画像", "备注", "时间", "来源", "接口", "前提", "结论", "人群", "周期", "专柜", "示例",
    "评估", "删除", "取消", "关闭", "发送", "话术", "考核", "频控", "暂无", "年龄", "收入", "人数", "门店", "未到", "收起", "会员"]);

  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const big = (num, unit) => { const v = parseFloat(num.replace(/,/g, "")) * unit, a = Math.abs(v);
    return a >= 1e9 ? `${(v / 1e9).toFixed(2)}B` : a >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : `${Math.round(v / 1e3).toLocaleString()}K`; };

  // ---------- 规则一：带数字的整句（先于短语替换） ----------
  const PRE = [
    [/^数据截至 (\S+)$/, "Data as of $1"],
    // 任务卡
    [/^(\d+) 天后$/, "In $1 days"],
    [/^联系 ([\d,]+) 位重点会员$/, "Contact $1 key members"],
    [/^导购人工回访，风险金额 (.+?)；智能体已按专柜派好任务$/, "Personal follow-up by associates, $1 at risk; the agent has already assigned tasks by counter"],
    [/^(.+)：([\d,]+) 人并入大促推送$/, "$1: $2 members merged into the promo push"],
    [/^([\d,]+) 位会员消费不及预期$/, "$1 members spending below expectation"],
    [/^([\d,]+) 人已排进定向触达$/, "$1 members queued for targeted outreach"],
    [/^补货提醒 ([\d,]+) 人$/, "Replenishment reminders: $1 members"],
    // 指标卡
    [/近 (\d+) 天活跃 ([\d,]+) · 客单价 (.+)$/, "$2 active in the last $1 days · AOV $3"],
    [/按生命周期分布 · 沉睡和流失占 (\d+%)/, "By lifecycle stage · dormant + lost = $1"],
    [/上季度同类预测与实际仅差 ([\d.]+%)/, "The same forecast last quarter was off by only $1"],
    [/高风险会员 ([\d,]+) 人/, "$1 high-risk members"],
    [/一年投入 (.+?) → 预测毛利 (.+)$/, "1-year cost $1 → predicted gross profit $2"],
    [/(\d+) 天内流失概率 ≥ (\d+%) 记为高风险；风险金额 = 流失概率 × 一年消费/, "High risk = churn probability ≥ $2 within $1 days; value at risk = churn probability × 1-year spend"],
    // 收入结构图
    [/^人数 ([\d,]+)（(\d+%)）$/, "Members $1 ($2)"],
    [/^累计收入 (.+?)（(\d+%)）$/, "Total revenue $1 ($2)"],
    [/^(.+?) (\d+%) 的会员贡献了 (\d+%) 的收入(?:；(.+?)占了 (\d+%) 的会员，收入只有 (\d+%))?。$/,
      (m, a, p1, p2, idle, p3, p4) => `${a} members are ${p1} of the base but bring in ${p2} of revenue` +
        (idle ? `; ${idle} members make up ${p3} of the base and only ${p4} of revenue.` : ".")],
    [/([\d,]+) 人 · 平均流失概率 (\d+%)/, "$1 members · avg churn probability $2"],
    [/^策略：/, "Strategy: "],
    [/^(\S+) 起 90 天预测收入$/, "90-day revenue forecast from $1:"],
    [/^后来确实流失（整体 (\d+%)）$/, "did churn afterwards (overall $1)"],
    [/^流失：在观察时点前 (\d+) 天内有消费、且之后 (\d+) 天内未再消费$/, "Churn: purchased within the $1 days before the observation date, then nothing in the following $2 days"],
    // 行动计划
    [/^([\d,]+) 人需要人工跟进，每周可跟进 (\d+) 人$/, "$1 members need a personal follow-up; capacity is $2 per week"],
    [/^第 (\d+) 周$/, "Wk $1"],
    [/^超出 (\d+) 周 → 转自动触达$/, "Beyond $1 wks → automated"],
    [/^前 (\d+) 周排进$/, "Scheduled in the first $1 weeks"],
    [/^(\d+) 月( \d+%)?$/, (m, n, p) => MONTHS[n - 1] + (p || "")],
    [/^(.+) · (\d+) 天后$/, "$1 · in $2 days"],
    [/^各月收入占全年 · 橙色为大促档期，大促前 (\d+) 天的自动挽留并入大促$/, "Monthly share of annual revenue · orange = promo windows; automated win-backs within $1 days before a promo are merged into it"],
    [/^触达规则：每人每周最多 (\d+) 次、每月 (\d+) 次 · 补货窗口内 ([\d,]+) 人 · 价格敏感会员占 (\d+%)，只有他们拿让利型权益$/,
      "Contact rules: at most $1 touches per member per week and $2 per month · $3 members in their replenishment window · price-sensitive members are $4, and only they get price-cut incentives"],
    [/^已显示 ([\d,]+) \/ 共 ([\d,]+) 条$/, "Showing $1 of $2"],
    [/^已显示 ([\d,]+) \/ 共 ([\d,]+) 人$/, "Showing $1 of $2 members"],
    [/^再加载 (\d+) 人$/, "Load $1 more"],
    // 导购任务弹窗
    [/^导购任务 · 会员 (\S+)$/, "Associate task · Member $1"],
    [/距上次到柜 (\d+) 天/, "$1 days since last visit"],
    [/（平时约 (\d+) 天一次）/, " (usually every ~$1 days)"],
    [/^原因：/, "Reason: "],
    [/^适用权益：/, "Incentive: "],
    [/^来源：/, "Source: "],
    [/本地模型 /, "local model "],
    [/大模型调用失败/, "LLM call failed"],
    [/^(.+?)不超过 (\d+) 字/, "$1: at most $2 characters"],
    [/出现：/, "found: "],
    [/(\d+) 元 > 上限 (\d+) 元/, "¥$1 > cap ¥$2"],
    [/(\d+) 字$/, "$1 characters"],
    [/^已生成 (\d+) 条请求（(.+?)）：(.+)。可在「预算与活动 › 支付宝对接」查看请求内容。$/, "Generated $1 request(s) ($2): $3. See the request under \"Budget & campaigns › Alipay integration\"."],
    [/^已生成 (\d+) 条请求（(.+?)）。/, "Generated $1 request(s) ($2). "],
    [/券活动限量 (\d+) 张（每位实验组会员 1 张），通过订阅消息通知领取；对照组不发/, "Coupon campaign capped at $1 coupons (one per treated member), announced by subscription message; nothing for the control group"],
    [/订阅消息按会员逐条发送，这里展示第 1 条，共 (\d+) 位实验组会员；对照组不发/, "Subscription messages are sent one per member; the first is shown here, $1 treated members in total; nothing for the control group"],
    [/^共 ([\d,]+) 条，点一行查看请求内容$/, "$1 in total; click a row to see the request"],
    // 预算与活动
    [/^全部 (\d+) 类人群$/, "All $1 segments"],
    [/^全部 (\d+) 类 ›$/, "All $1 segments ›"],
    [/；羊毛党 (\d+) 人不发券/, "; $1 deal hunters get no coupons"],
    [/([\d,]+) 人 · 人均成本 (.+)$/, "$1 members · cost per member $2"],
    [/^预测毛利 (.+?) · 回报 (.+)$/, "Predicted gross profit $1 · return $2"],
    [/^建议：/, "Advice: "],
    [/^活动：/, "Campaign: "],
    [/^：(.+)，每人投入 (.+?)（动作投入 \+ 权益 × 核销率）$/, ": $1, cost per member $2 (action cost + incentive × redemption rate)"],
    [/^两组会员底子相近（活动组 \/ 对照组）：(.+?)。按目前人数，消费提升超过 (\d+%) 才能稳定看出来。\s*(?:已排除 (\d+) 位正在参加其他活动的会员。)?$/,
      (m, bal, lift, ex) => `The two groups start out similar (treated / control): ${bal}. At the current group size, only a lift above ${lift} can be detected reliably.` +
        (ex ? ` ${ex} members already in other campaigns were excluded.` : "")],
    [/^实际 (.+?) \/ 预期 (.+)$/, "Actual $1 / expected $2"],
    [/^（大概率在 (.+?) ~ (.+?) 之间）；\s*额外毛利 (.+?)，额外成本 (.+?)，实测回报$/, "(most likely between $1 and $2); incremental gross profit $3, incremental cost $4, measured return"],
    [/^活动 (\S+) 开始，数据截至 (\S+)，等数据更新后再评估$/, "The campaign starts $1 and the data runs to $2; evaluate once the data is updated"],
    [/^由 (.+) 重写，数字来自看板的计算结果$/, "Rewritten by $1; numbers come from the dashboard's calculations"],
    [/，已显示模板简报/, "; showing the template brief"],
    [/^重写失败：/, "Rewrite failed: "],
    // 价值洞察
    [/^在高价值会员中占 (\d+%)，在全体中占 (\d+%)$/, "$1 of high-value members vs. $2 of all members"],
    [/^(\S+) 至 (\S+) 的全部流水$/, "All transactions from $1 to $2"],
    [/^会员客单价比非会员(高|低) (\d+%)(，但每单件数更少，连带销售还有空间)?。$/,
      (m, d, p, more) => `Members' AOV is ${p} ${d === "高" ? "higher" : "lower"} than non-members'` + (more ? ", but they buy fewer items per order, so there's room to cross-sell." : ".")],
    [/^首单在 (\S+) 及以前的 ([\d,]+) 位会员 · 首年人均消费，虚线为整体平均 (.+)$/, "$2 members whose first order was on or before $1 · first-year spend per member; dashed line = overall average $3"],
    [/^(\d+%) 成高价值 · 羊毛党 (\d+%)$/, "$1 high-value · $2 deal hunters"],
    [/^入会不久、画像接近高价值会员，预测未来 (\d+) 年可贡献 (.+)$/, "Recently joined, with a profile close to high-value members; predicted to contribute $2 over the next $1 years"],
    [/^(\d+)-(\d+)岁$/, "$1–$2"],
    [/^(\d+)岁以上$/, "$1+"],
    [/^(\d+)岁以下$/, "Under $1"],
    [/^(\d+)-(\d+)年$/, "$1–$2 yrs"],
    [/^(\d+)年以上$/, "$1+ yrs"],
    [/^入会不满(\d+)年$/, "Under $1 yr"],
    // 人群侧栏
    [/^人均 (.+?)（预测年消费 × (.+?)）$/, "Avg $1 per member (predicted annual spend × $2)"],
    [/^平均流失概率 (\d+%)$/, "Avg churn probability $1"],
    [/^收入占比 (\d+%)$/, "Share of revenue $1"],
    // 会员详情
    [/^会员 (\S+)$/, "Member $1"],
    [/^推荐：/, "Recommended: "],
    [/^(\d+)天流失概率$/, "$1-day churn probability"],
    [/^预测未来(\d+)天消费$/, "Predicted spend, next $1 days"],
    [/^预测未来(\d+)年消费金额（折现）$/, "Predicted spend, next $1 years (discounted)"],
    // 下一步动作
    [/仅在「(.+?)」触达/, "Outreach only during \"$1\""],
    [/：导购人工回访（排在第 (\d+) 周）/, ": personal follow-up by an associate (scheduled for week $1)"],
    [/：导购人工回访/, ": personal follow-up by an associate"],
    [/未达预期（预期 ([\d.]+) 次，实际 (\d+) 次）：/, "Below expectation (expected $1 purchases, actual $2): "],
    [/补货提醒（(\d+) 天周期）/, "Replenishment reminder ($1-day cycle)"],
    [/：并入「(.+?)」推送/, ": merged into the \"$1\" push"],
    [/：自动触达/, ": automated outreach"],
    [/：按入会天数节点触达/, ": outreach at days-since-joining milestones"],
    // 流失 / 未达预期信号
    [/已沉默 (\d+) 天，是其平均复购间隔的 ([\d.]+) 倍/g, "Silent for $1 days, $2× their average repurchase gap"],
    [/已沉默 (\d+) 天，超过平时间隔的 ([\d.]+) 倍/g, "Silent for $1 days, more than $2× their usual gap"],
    [/近 (\d+) 天没有任何消费/g, "No purchases in the last $1 days"],
    [/近 (\d+) 天消费比之前 (\d+) 天下降 (\d+%)/g, "Spend in the last $1 days down $3 vs. the previous $2 days"],
    [/按以往购买节奏，仍是活跃顾客的可能性仅 (\d+%)/g, "Going by past purchase rhythm, only $1 likely to still be active"],
    [/综合消费行为判断，(\d+) 天内不再消费的可能性为 (\d+%)/g, "Overall behaviour points to a $2 chance of no purchase within $1 days"],
    [/检查点后 (\d+) 天没有到柜/g, "No counter visit in the $1 days since the checkpoint"],
    [/客单从 ([\d,.]+) 降到 ([\d,.]+)/g, "Basket size fell from $1 to $2"],
    [/购买品牌从 (\d+) 个减到 (\d+) 个，可能部分品类转去别处买/g, "Brands purchased fell from $1 to $2; some categories may have moved elsewhere"],
    // 运营助手
    [/^正在看：/, "Viewing: "],
    [/^整理结果中… /, "Compiling results… "],
    [/^思考中… /, "Thinking… "],
    [/^用时 (\d+) 秒$/, "Took $1s"],
    [/^没能完成回答：(.*)，请稍后再试。$/, "Couldn't finish the answer: $1. Please try again later."],
    // 简报（模板）
    [/^本周要做的事（观察日 (\S+)）$/, "This week's to-dos (as of $1)"],
    [/^导购任务 ([\d,]+) 条，最多的专柜：(.+?)。人工回访需求 ([\d,]+) 人，产能每周 (\d+) 人，([\d,]+) 人已转自动触达。$/,
      (m, n, counters, demand, cap, over) => `${n} associate tasks; busiest counters: ${counters.replace(/ 人/g, "").replace(/、/g, ", ")}. ` +
        `${demand} members need a personal follow-up against a capacity of ${cap} per week; ${over} have been switched to automated outreach.`],
    [/^高价值会员里有 ([\d,]+) 人付费没达到预期，主要原因：(.+)。建议导购本周优先跟进。$/,
      (m, n, reasons) => `${n} high-value members are spending below expectation. Main reasons: ${reasons.replace(/（(\d+) 人）/g, " ($1)")}. Associates should follow up with them first this week.`],
    [/^近期档期：(.+)。档期前 (\d+) 天内的自动挽留已并入大促推送，不单独让利。$/,
      (m, ev, d) => `Upcoming promos: ${ev.replace(/(\S+) (\d\d-\d\d) 起（(\d+) 天后）/g, "$1 from $2 (in $3 days)")}. ` +
        `Automated win-backs within ${d} days before a promo are merged into the promo push, with no separate price cuts.`],
    [/^一年运营成本 (.+?)，整体用户 ROI ([\d.]+) 倍；高价值会员占成本 (\d+%)、占预测毛利 (\d+%)。$/,
      "1-year operating cost $1, overall member ROI $2×; high-value members account for $3 of cost and $4 of predicted gross profit."],
    [/^羊毛党中 ([\d,]+) 人毛利抵不过发券成本，本周不发券。$/, "$1 deal hunters' gross profit can't cover the coupon cost; no coupons for them this week."],
    [/^活动已结束待评估：(.+)。$/, "Campaigns ended and awaiting evaluation: $1."],
    [/^进行中的活动：(.+)，对照组会员活动期间不触达。$/, "Running campaigns: $1; control-group members are not contacted during the campaign."],
    [/^导购任务还有 ([\d,]+) 条待处理；话术由智能体起草，导购确认后才会生成支付宝请求。$/,
      "$1 associate tasks are still pending; scripts are drafted by the agent, and Alipay requests are generated only after the associate confirms."],
  ];

  // ---------- 规则二：单位与标点（在短语替换之后） ----------
  const postRules = svg => [
    [/([¥$])([\d,.]+) 亿/g, (m, c, n) => c + big(n, 1e8)],
    [/([¥$])([\d,.]+) 万/g, (m, c, n) => c + big(n, 1e4)],
    [/([\d,.]+) 倍/g, "$1×"],
    [/（([\d,]+) 人）/g, " ($1)"],
    [/([\d,]+) 人/g, svg ? "$1 ppl" : "$1 members"],
    [/(\d+) 天/g, "$1 days"],
    [/(\d+) 次/g, "$1 times"],
    [/([A-Za-z]) ?柜/g, "$1"],
    [/，/g, ", "], [/。/g, ". "], [/；/g, "; "], [/：/g, ": "], [/、/g, ", "],
    [/（/g, " ("], [/）/g, ") "], [/[「“]/g, " \""], [/[」”]/g, "\" "], [/～/g, "~"], [/＋/g, "+"],
  ];
  const POST = {false: postRules(false), true: postRules(true)};

  const escRe = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const PHRASES = Object.keys(D).filter(k => !EXACT_ONLY.has(k)).sort((a, b) => b.length - a.length);
  const PHRASE_RE = new RegExp(PHRASES.map(escRe).join("|"), "g");
  const CJK = /[　-〿㐀-鿿＀-￯]/;
  const cache = new Map();

  function toEn(text, svg) {
    if (!CJK.test(text)) return text;
    const key = (svg ? "s|" : "h|") + text;
    if (cache.has(key)) return cache.get(key);
    const [, lead, core, tail] = text.match(/^(\s*)([\s\S]*?)(\s*)$/);
    let s;
    if (Object.prototype.hasOwnProperty.call(D, core)) s = D[core];
    else {
      s = core;
      for (const [re, rep] of PRE) s = s.replace(re, rep);
      s = s.replace(PHRASE_RE, m => ` ${D[m]} `);
      for (const [re, rep] of POST[!!svg]) s = s.replace(re, rep);
      s = s.replace(/\s+/g, " ").replace(/ ([,.;:)%])/g, "$1").replace(/\( /g, "(").replace(/"\s*([^"]*?)\s*"/g, "\"$1\"").trim();
    }
    const out = lead + s + tail;
    if (cache.size > 5000) cache.clear();
    cache.set(key, out);
    return out;
  }

  // ---------- DOM 层 ----------
  const ATTRS = ["placeholder", "aria-label", "title"];
  const textOrig = new WeakMap();   // 文本节点 -> {zh, en}
  const attrOrig = new WeakMap();   // 元素 -> {属性名: {zh, en}}
  let lang = "zh";
  try { lang = localStorage.getItem("lang") === "en" ? "en" : "zh"; } catch (e) {}

  const skipped = (el, sel = "[data-noi18n], script, style") => !el || !!el.closest(sel);

  function textNode(n) {
    const p = n.parentElement;
    if (skipped(p, "[data-noi18n], script, style, textarea")) return;
    const cur = n.nodeValue, rec = textOrig.get(n);
    if (lang === "en") {
      if (rec && rec.en === cur) return;
      const en = toEn(cur, p.namespaceURI === "http://www.w3.org/2000/svg");
      if (en !== cur) { textOrig.set(n, {zh: cur, en}); n.nodeValue = en; }
    } else if (rec && rec.en === cur) { textOrig.delete(n); n.nodeValue = rec.zh; }
  }

  function element(el) {
    if (skipped(el)) return;
    for (const a of ATTRS) {
      if (!el.hasAttribute(a)) continue;
      const cur = el.getAttribute(a), recs = attrOrig.get(el) || {}, rec = recs[a];
      if (lang === "en") {
        if (rec && rec.en === cur) continue;
        const en = toEn(cur, false);
        if (en !== cur) { recs[a] = {zh: cur, en}; attrOrig.set(el, recs); el.setAttribute(a, en); }
      } else if (rec && rec.en === cur) { delete recs[a]; el.setAttribute(a, rec.zh); }
    }
    // 表单里的默认值（如活动名称）跟着语言走；用户改过的不动
    if (el.tagName === "INPUT" && el.defaultValue && CJK.test(el.defaultValue)) {
      const en = toEn(el.defaultValue, false);
      if (lang === "en" && el.value === el.defaultValue) el.value = en;
      else if (lang === "zh" && el.value === en) el.value = el.defaultValue;
    }
  }

  function walk(root) {
    if (root.nodeType === 3) return textNode(root);
    if (root.nodeType !== 1) return;
    element(root);
    const w = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) n.nodeType === 3 ? textNode(n) : element(n);
  }

  const observer = new MutationObserver(list => {
    if (lang !== "en") return;
    for (const m of list) {
      if (m.type === "characterData") textNode(m.target);
      else if (m.type === "attributes") element(m.target);
      else m.addedNodes.forEach(walk);
    }
  });

  function setLang(next) {
    lang = next === "en" ? "en" : "zh";
    try { localStorage.setItem("lang", lang); } catch (e) {}
    document.documentElement.lang = lang === "en" ? "en" : "zh-CN";
    document.querySelectorAll("[data-lang]").forEach(b => b.setAttribute("aria-pressed", b.dataset.lang === lang));
    walk(document.documentElement);
    observer.takeRecords();
    document.dispatchEvent(new CustomEvent("langchange", {detail: lang}));
  }

  function init() {
    observer.observe(document.documentElement, {subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS});
    document.querySelectorAll("[data-lang]").forEach(b => b.onclick = () => setLang(b.dataset.lang));
    setLang(lang);
  }

  window.I18N = {
    get lang() { return lang; },
    set: setLang,
    init,
    // 给 confirm() 这类不经过 DOM 的文案用
    t: s => lang === "en" ? toEn(s, false) : s,
  };
})();
