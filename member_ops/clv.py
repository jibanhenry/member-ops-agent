"""付费预测：BG/NBD（未来购买次数 + 存活概率）× Gamma-Gamma（客单价）= 预测 CLV。

两者都是经典的概率模型（Fader, Hardie & Lee 2005；Fader & Hardie 2013），
参数少、可解释，适合会员数不大的中小商家。这里直接用 scipy 实现，不依赖已停止维护的 lifetimes 库。
时间单位：周。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import betaln, gammaln, hyp2f1


@dataclass
class BGNBD:
    r: float = 1.0
    alpha: float = 1.0
    a: float = 1.0
    b: float = 1.0

    @staticmethod
    def _neg_ll(log_params, x, t_x, T):
        r, alpha, a, b = np.exp(log_params)
        common = gammaln(r + x) - gammaln(r) + r * np.log(alpha)
        term1 = betaln(a, b + x) - betaln(a, b) - (r + x) * np.log(alpha + T)
        with np.errstate(divide="ignore", invalid="ignore"):
            term2 = np.where(
                x > 0,
                betaln(a + 1, b + x - 1) - betaln(a, b) - (r + x) * np.log(alpha + t_x),
                -np.inf,
            )
        return -(common + np.logaddexp(term1, term2)).sum()

    def fit(self, x, t_x, T) -> "BGNBD":
        x, t_x, T = map(np.asarray, (x, t_x, T))
        res = minimize(self._neg_ll, x0=np.log([1.0, max(T.mean(), 1.0), 1.0, 1.0]),
                       args=(x, t_x, T), method="L-BFGS-B")
        self.r, self.alpha, self.a, self.b = np.exp(res.x)
        return self

    def p_alive(self, x, t_x, T):
        x, t_x, T = map(np.asarray, (x, t_x, T))
        r, alpha, a, b = self.r, self.alpha, self.a, self.b
        with np.errstate(divide="ignore", invalid="ignore"):
            odds = np.where(x > 0, a / (b + x - 1) * ((alpha + T) / (alpha + t_x)) ** (r + x), 0.0)
        return 1.0 / (1.0 + odds)

    def expected_purchases(self, t, x, t_x, T):
        """未来 t 周内的期望购买次数。"""
        x, t_x, T = map(np.asarray, (x, t_x, T))
        r, alpha, a, b = self.r, self.alpha, self.a, self.b
        z = t / (alpha + T + t)
        hyp = hyp2f1(r + x, b + x, a + b + x - 1, z)
        numer = (a + b + x - 1) / (a - 1) * (1 - ((alpha + T) / (alpha + T + t)) ** (r + x) * hyp)
        with np.errstate(divide="ignore", invalid="ignore"):
            odds = np.where(x > 0, a / (b + x - 1) * ((alpha + T) / (alpha + t_x)) ** (r + x), 0.0)
        return numer / (1 + odds)


@dataclass
class GammaGamma:
    p: float = 1.0
    q: float = 1.0
    v: float = 1.0

    @staticmethod
    def _neg_ll(log_params, x, m):
        p, q, v = np.exp(log_params)
        ll = (gammaln(p * x + q) - gammaln(p * x) - gammaln(q) + q * np.log(v)
              + (p * x - 1) * np.log(m) + p * x * np.log(x) - (p * x + q) * np.log(x * m + v))
        return -ll.sum()

    def fit(self, x, m) -> "GammaGamma":
        x, m = np.asarray(x, float), np.asarray(m, float)
        # 以群体均值初始化尺度参数 v，Nelder-Mead 在这里容易跑到 p→∞ 的退化解
        res = minimize(self._neg_ll, x0=np.log([2.0, 3.0, m.mean()]), args=(x, m), method="L-BFGS-B")
        self.p, self.q, self.v = np.exp(res.x)
        return self

    def expected_spend(self, x, m, population_mean: float):
        """条件期望客单价；无复购记录的会员退化为群体均值。"""
        x, m = np.asarray(x, float), np.asarray(m, float)
        p, q, v = self.p, self.q, self.v
        with np.errstate(divide="ignore", invalid="ignore"):
            cond = p * (v + x * m) / (p * x + q - 1)
        return np.where((x > 0) & np.isfinite(cond), cond, population_mean)


def fit_and_score(snap: pd.DataFrame, horizon_days: tuple[int, ...] = (90, 365)) -> tuple[pd.DataFrame, dict]:
    """在快照上拟合两个模型，返回每位会员的 p_alive / 预测客单价，以及各时间窗的预测购买次数和 CLV。"""
    bg = BGNBD().fit(snap["bg_x"], snap["bg_t_x"], snap["bg_T"])

    repeaters = snap[(snap["bg_x"] > 0) & (snap["gg_monetary"] > 0)]
    gg = GammaGamma().fit(repeaters["bg_x"], repeaters["gg_monetary"])

    out = pd.DataFrame(index=snap.index)
    out["p_alive"] = bg.p_alive(snap["bg_x"], snap["bg_t_x"], snap["bg_T"])
    out["pred_aov"] = gg.expected_spend(
        snap["bg_x"], snap["gg_monetary"].fillna(0), population_mean=float(repeaters["gg_monetary"].mean())
    )
    for d in horizon_days:
        purchases = bg.expected_purchases(d / 7.0, snap["bg_x"], snap["bg_t_x"], snap["bg_T"])
        out[f"pred_purchases_{d}d"] = purchases
        out[f"pred_clv_{d}d"] = purchases * out["pred_aov"]
    params = {"bgnbd": vars(bg), "gamma_gamma": vars(gg)}
    return out, params
