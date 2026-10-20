"""会员运营 Agent 原型。

数据流：原始交易 -> data.load_transactions (标准化订单表)
        -> features.build_snapshot (客户级特征)
        -> segments / clv / churn (画像、生命周期、付费预测、流失预警)
        -> pipeline.run (落盘 data/processed)
        -> tools / agent (对话式运营助手)
"""
