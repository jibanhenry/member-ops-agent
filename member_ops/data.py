"""原始数据 -> 标准会员系统表。

标准订单表（一行一笔消费）字段：
    customer_id  会员 ID
    order_id     订单/小票 ID
    order_time   消费时间
    amount       实付金额
    n_lines      商品行数
    n_skus       SKU 数
    discount     优惠总额（标价 - 实付），可选
    coupon_disc  用券抵扣金额，可选
    points       本单获得积分，可选

会员档案表（一行一位会员，可选）：人口属性、入会时间、品牌/品类偏好、营销触达等。

任何商家的会员系统只要能导出这两张表，就能走同一条管线。
不同业态的消费频率差别很大，特征窗口和流失定义放在 DatasetConfig 里按数据集配置。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"
CJ_DIR = RAW_DIR / "completejourney"
DEPT_DIR = RAW_DIR / "dept_store"


@dataclass(frozen=True)
class DatasetConfig:
    key: str
    info: dict
    activity_windows: tuple[int, ...]   # 近 N 天活跃度特征
    trend_window: int                   # 消费趋势：近 N 天 vs 之前 N 天
    quiet_window: int                   # 流失信号"近 N 天没有消费"
    churn_horizon_days: int             # 流失定义：之后 N 天不再消费
    active_window_days: int             # 只对近 N 天有消费的会员做流失建模
    extra: dict = field(default_factory=dict)


DEPT_STORE = DatasetConfig(
    key="dept_store",
    info={
        "name": "2018 年全国大学生数学建模竞赛 C 题·大型百货商场会员数据",
        "license": "竞赛公开附件，仅用于非商业演示",
        "url": "http://www.mcm.edu.cn/html_cn/node/7cec7725b9a0ea07b4dfd175e8042c33.html",
        "description": "国内一家大型百货商场的会员系统数据（以高端美妆专柜为主）：会员档案（出生日期、性别、入会时间）"
                       "与每一笔消费的实付金额、折扣、积分、专柜（公开数据集，仅作演示）",
        "currency": "CNY",
    },
    activity_windows=(30, 90, 180),
    trend_window=180,
    quiet_window=180,
    churn_horizon_days=180,
    active_window_days=365,
)

COMPLETE_JOURNEY = DatasetConfig(
    key="completejourney",
    info={
        "name": "84.51° / dunnhumby The Complete Journey",
        "license": "CC0（completejourney R 包）",
        "url": "https://github.com/bradleyboehmke/completejourney",
        "description": "美国一家零售商的会员卡系统数据：2,469 位会员 2017 全年的每一笔消费、"
                       "优惠/用券、营销活动触达与家庭画像（公开数据集，仅作演示）",
        "currency": "USD",
    },
    activity_windows=(14, 30, 90),
    trend_window=60,
    quiet_window=30,
    churn_horizon_days=60,
    active_window_days=90,
)

DATASETS = {c.key: c for c in (DEPT_STORE, COMPLETE_JOURNEY)}
CONFIG = DATASETS[os.environ.get("MEMBER_DATASET", "dept_store")]
DATASET_INFO = CONFIG.info


def load_dataset() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """返回 (orders, profiles, extra_report)，结果按数据集缓存在 data/processed。"""
    orders_cache = PROCESSED_DIR / f"{CONFIG.key}_orders.parquet"
    profiles_cache = PROCESSED_DIR / f"{CONFIG.key}_profiles.parquet"
    extra_cache = PROCESSED_DIR / f"{CONFIG.key}_extra.json"
    if orders_cache.exists() and profiles_cache.exists():
        extra = json.loads(extra_cache.read_text(encoding="utf-8")) if extra_cache.exists() else {}
        return pd.read_parquet(orders_cache), pd.read_parquet(profiles_cache), extra

    loader = {"dept_store": _load_dept_store, "completejourney": _load_complete_journey}[CONFIG.key]
    orders, profiles, extra = loader()
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    orders.to_parquet(orders_cache, index=False)
    profiles.to_parquet(profiles_cache)
    extra_cache.write_text(json.dumps(extra, ensure_ascii=False), encoding="utf-8")
    return orders, profiles, extra


# ---------- 百货商场（2018 国赛 C 题） ----------

# 会员卡号完整、连续的时间段。2015-09~11 流水缺失，2016-01~03 绝大多数流水没有记录卡号，
# 把缺口当成"会员没来"会把所有人误判为流失，所以只用之后的连续数据建模。
DEPT_START = pd.Timestamp("2016-04-01")
DEPT_END = pd.Timestamp("2018-01-01")
# 2017-10 起流水只剩会员消费，会员/非会员对比只能用两者都有的时段
DEPT_COMPARE_END = pd.Timestamp("2017-10-01")


def _load_dept_store() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    members = pd.read_excel(DEPT_DIR / "项目数据1.xlsx", dtype={"kh": str}).drop_duplicates("kh").set_index("kh")
    lines = pd.read_csv(
        DEPT_DIR / "项目数据2.csv",
        dtype={"kh": str, "spbm": str, "syjh": str, "djh": str, "gzbm": str, "gzmc": str},
    )
    lines["dtime"] = pd.to_datetime(lines["dtime"], errors="coerce")
    for c in ("sl", "sj", "je", "jf"):
        lines[c] = pd.to_numeric(lines[c], errors="coerce")
    lines = lines.dropna(subset=["dtime", "je"])
    lines = lines[(lines["dtime"] >= DEPT_START) & (lines["dtime"] < DEPT_END)]
    # 标价 - 实付 = 折扣；退货行（数量/金额为负）不计折扣
    lines["discount"] = (lines["sj"] * lines["sl"] - lines["je"]).clip(lower=0)

    extra = {"member_value": _member_vs_nonmember(lines)}

    # 只保留会员档案里有的卡（流水里其余卡号属于其他分店）
    mem = lines[lines["kh"].isin(members.index)]
    # 单据号会重复使用，用 卡号+收银机+单据号+时间 识别一笔消费
    key = ["kh", "syjh", "djh", "dtime"]
    orders = (
        mem.groupby(key, as_index=False, dropna=False)
        .agg(amount=("je", "sum"), n_lines=("spbm", "size"), n_skus=("spbm", "nunique"),
             discount=("discount", "sum"), points=("jf", "sum"), brand=("gzmc", "first"))
        .rename(columns={"kh": "customer_id", "dtime": "order_time"})
    )
    orders = orders[orders["amount"] > 0]  # 整单退货
    orders["order_id"] = (orders["syjh"].astype(str) + "-" + orders["djh"].astype(str) + "-"
                          + orders["order_time"].dt.strftime("%Y%m%d%H%M%S"))
    orders = orders.drop(columns=["syjh", "djh"]).sort_values(["customer_id", "order_time"], ignore_index=True)
    orders[["amount", "discount", "points"]] = orders[["amount", "discount", "points"]].round(2)

    profiles = _dept_profiles(members.loc[members.index.isin(orders["customer_id"])], mem)
    return orders, profiles, extra


def _dept_profiles(members: pd.DataFrame, mem_lines: pd.DataFrame) -> pd.DataFrame:
    snapshot = DEPT_END
    birth = pd.to_datetime(members["csrq"], errors="coerce")
    age = ((snapshot - birth).dt.days / 365.25).where(lambda a: a.between(16, 90))
    reg = pd.to_datetime(members["djsj"], errors="coerce")
    reg = reg.where((reg >= "1995-01-01") & (reg < snapshot))
    # 性别编码数据字典未给出含义；买家中 0 占约 90%，结合美妆专柜为主，推断 0=女、1=男
    gender = members["xb"].map({0: "女", 1: "男"})

    profiles = pd.DataFrame({
        "age": age.round(0),
        "gender": gender,
        "reg_date": reg,
        "member_years": ((snapshot - reg).dt.days / 365.25).round(1),
    })
    profiles["age_band"] = pd.cut(profiles["age"], [0, 25, 35, 45, 55, 200],
                                  labels=["25岁以下", "25-34岁", "35-44岁", "45-54岁", "55岁以上"], right=False).astype(str)
    profiles["tenure_band"] = pd.cut(profiles["member_years"], [0, 1, 3, 5, 100],
                                     labels=["入会不满1年", "1-3年", "3-5年", "5年以上"], right=False).astype(str)
    profiles[["age_band", "tenure_band"]] = profiles[["age_band", "tenure_band"]].replace("nan", pd.NA)

    by_brand = mem_lines.groupby(["kh", "gzmc"])["je"].sum().reset_index()
    top = by_brand.sort_values("je").groupby("kh").tail(1).set_index("kh")["gzmc"]
    profiles["top_brand"] = top
    profiles["n_brands"] = by_brand[by_brand["je"] > 0].groupby("kh").size()
    profiles["points_total"] = mem_lines.groupby("kh")["jf"].sum().round(0)
    profiles.index.name = "customer_id"
    return profiles


def _member_vs_nonmember(lines: pd.DataFrame) -> dict:
    """会员 vs 非会员：同一时段内的客单价、件单价、收入占比（赛题第 1 问）。"""
    win = lines[lines["dtime"] < DEPT_COMPARE_END]
    win = win.assign(is_member=win["kh"].notna())
    orders = win.groupby(["is_member", "syjh", "djh", "dtime"], dropna=False).agg(
        amount=("je", "sum"), qty=("sl", "sum")).reset_index()
    orders = orders[orders["amount"] > 0]
    g = orders.groupby("is_member")
    stats = pd.DataFrame({"orders": g.size(), "revenue": g["amount"].sum(), "aov": g["amount"].mean(),
                          "items_per_order": g["qty"].mean()})
    stats["revenue_share"] = stats["revenue"] / stats["revenue"].sum()
    out = {("member" if k else "non_member"): {c: round(float(v), 2) for c, v in row.items()}
           for k, row in stats.iterrows()}
    out["period"] = [str(DEPT_START.date()), str((DEPT_COMPARE_END - pd.Timedelta(days=1)).date())]
    return out


# ---------- dunnhumby The Complete Journey（备用数据集） ----------

def _load_complete_journey() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    orders, profiles = load_complete_journey()
    return orders, profiles, {}


def _read(name: str) -> pd.DataFrame:
    import pyreadr

    return next(iter(pyreadr.read_r(CJ_DIR / name).values()))


def load_complete_journey() -> tuple[pd.DataFrame, pd.DataFrame]:
    tx = _read("transactions.rds")
    tx = tx[tx["sales_value"] > 0]
    tx = tx.assign(discount=tx["retail_disc"] + tx["coupon_disc"] + tx["coupon_match_disc"])

    orders = (
        tx.groupby(["household_id", "basket_id"], as_index=False, observed=True)
        .agg(
            order_time=("transaction_timestamp", "min"),
            amount=("sales_value", "sum"),
            n_lines=("product_id", "size"),
            n_skus=("product_id", "nunique"),
            discount=("discount", "sum"),
            coupon_disc=("coupon_disc", "sum"),
            store_id=("store_id", "first"),
        )
        .rename(columns={"household_id": "customer_id", "basket_id": "order_id"})
        .sort_values(["customer_id", "order_time"], ignore_index=True)
    )
    orders[["amount", "discount", "coupon_disc"]] = orders[["amount", "discount", "coupon_disc"]].round(2)
    orders["customer_id"] = orders["customer_id"].astype(str)
    orders["order_id"] = orders["order_id"].astype(str)

    return orders, _build_profiles(tx)


def _build_profiles(tx: pd.DataFrame) -> pd.DataFrame:
    """会员档案：家庭画像 + 营销触达 + 用券 + 品类偏好。"""
    products = _read("products.rda")[["product_id", "department", "brand"]]
    lines = tx[["household_id", "product_id", "sales_value", "store_id"]].merge(products, on="product_id", how="left")

    by_dept = lines.groupby(["household_id", "department"], observed=True)["sales_value"].sum().reset_index()
    top_dept = by_dept.sort_values("sales_value").groupby("household_id").tail(1).set_index("household_id")["department"]
    private = lines.assign(p=lines["sales_value"].where(lines["brand"] == "Private", 0)).groupby("household_id")
    profiles = pd.DataFrame({
        "top_department": top_dept,
        "private_label_share": private["p"].sum() / private["sales_value"].sum(),
        "n_stores": lines.groupby("household_id")["store_id"].nunique(),
    })

    demo = _read("demographics.rda").set_index("household_id")
    profiles = profiles.join(demo.astype(str).replace("nan", pd.NA))

    campaigns = _read("campaigns.rda").groupby("household_id").size().rename("campaigns_received")
    redemptions = _read("coupon_redemptions.rda").groupby("household_id").size().rename("coupons_redeemed")
    profiles = profiles.join(campaigns).join(redemptions)
    profiles[["campaigns_received", "coupons_redeemed"]] = (
        profiles[["campaigns_received", "coupons_redeemed"]].fillna(0).astype(int)
    )
    profiles.index = profiles.index.astype(str)
    profiles.index.name = "customer_id"
    return profiles


def load_orders_csv(path: Path, column_map: dict[str, str] | None = None) -> pd.DataFrame:
    """读取商家自有会员消费 CSV。column_map 把商家列名映射到标准字段名，
    例如 {"会员号": "customer_id", "订单号": "order_id", "下单时间": "order_time", "实付金额": "amount"}。
    """
    df = pd.read_csv(path)
    if column_map:
        df = df.rename(columns=column_map)
    missing = {"customer_id", "order_id", "order_time", "amount"} - set(df.columns)
    if missing:
        raise ValueError(f"订单表缺少字段: {sorted(missing)}")
    df["customer_id"] = df["customer_id"].astype(str)
    df["order_id"] = df["order_id"].astype(str)
    df["order_time"] = pd.to_datetime(df["order_time"])
    df = df[df["amount"] > 0]
    for col in ("n_lines", "n_skus"):
        if col not in df.columns:
            df[col] = 1
    return df.sort_values(["customer_id", "order_time"], ignore_index=True)
