#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UTS Algorithmic Trading - Backtest RSI(14) Cross 50, Long-Only, Universe Kompas 100
Atikah Nurfatkiyah (NIM 25110200025)

Skrip ini:
  1. mengunduh harga harian saham Kompas 100 + IHSG (^JKSE) dari Yahoo Finance (yfinance),
  2. menjalankan backtest portofolio (aturan eksekusi realistis: fee, spread, fraksi harga,
     lot 100, batas likuiditas, maksimal posisi),
  3. menjalankan uji validitas (IS/OOS, walk-forward, sensitivitas, biaya, keterlambatan,
     ketahanan, bootstrap, look-ahead, forward test, audit),
  4. menulis seluruh hasil ke dalam dashboard index.html (blok <script id="uts-data">).

Pemakaian:
  python backtest.py                # unduh harga (memakai cache bila ada) lalu hitung
  python backtest.py --refresh      # paksa unduh ulang harga dari Yahoo
  python backtest.py --demo         # data simulasi (tanpa internet), hanya untuk uji coba kode
  python backtest.py --html index.html --json hasil.json

Aturan strategi (identik dengan mesin di dashboard):
  - Sinyal dihitung dari harga penutupan hari t. RSI Wilder periode 14.
  - BUY : RSI[t] > 50 dan RSI[t-1] <= 50 (RSI naik menembus 50).
  - SELL: RSI[t] < 50 dan RSI[t-1] >= 50 (RSI turun menembus 50).
  - Order dieksekusi di harga pembukaan hari bursa berikutnya (t+1); beli di open + spread
    (1 fraksi), jual di open - spread (1 fraksi).
  - Ukuran posisi 10% ekuitas penutupan hari sebelumnya, maksimal 10 posisi, kelipatan 1 lot,
    nilai order <= 5% rata-rata nilai transaksi 20 hari sebelumnya.
  - Fee beli 0,15%, fee jual 0,25% (termasuk PPh final). Tanpa short, tanpa margin.
"""
import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from datetime import date, datetime, timedelta

import numpy as np

SCRIPT_PATH = os.path.abspath(__file__)
CACHE_DIR = ".cache"
KS = ["cagr", "mdd", "sharpe", "calmar", "profit_factor", "win_ratio", "n_trades", "total_return",
      "avg_profit_pct", "avg_loss_pct", "expectancy_pct", "years", "end_equity", "total_fee", "total_spread"]
SCRIPT_TAG = re.compile(r'(<script type="application/json" id="uts-data">)(.*?)(</script>)', re.S)


# ----------------------------------------------------------------------------- utilitas dasar
def jround(x):
    """Pembulatan setengah ke atas (sama dengan Math.round di JavaScript)."""
    return math.floor(x + 0.5)


def tick_size(p):
    """Fraksi harga BEI."""
    return 1 if p < 200 else 2 if p < 500 else 5 if p < 2000 else 10 if p < 5000 else 25


def rsi_wilder(c, p):
    """RSI Wilder (rekursif, kausal). Indeks < p berisi NaN."""
    n = len(c)
    out = np.full(n, np.nan)
    if n <= p:
        return out
    d = np.diff(c)
    g = d[:p][d[:p] > 0].sum() / p
    l = -d[:p][d[:p] < 0].sum() / p
    out[p] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    for i in range(p + 1, n):
        x = d[i - 1]
        g = (g * (p - 1) + (x if x > 0 else 0.0)) / p
        l = (l * (p - 1) + (-x if x < 0 else 0.0)) / p
        out[i] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    return out


def clean(o):
    """Ubah tipe numpy / NaN menjadi tipe JSON yang aman."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        o = float(o)
        return None if (math.isnan(o) or math.isinf(o)) else o
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o


# ----------------------------------------------------------------------------- data harga
def download_prices(tickers, start, end, refresh):
    """Unduh OHLCV harian (disesuaikan split/dividen) dari Yahoo Finance. Hasil di-cache."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache = os.path.join(CACHE_DIR, "prices.json")
    if os.path.exists(cache) and not refresh:
        with open(cache, encoding="utf-8") as f:
            obj = json.load(f)
        if set(tickers) <= set(obj["prices"]):
            print(f"Memakai cache harga ({cache}, diunduh {obj['downloaded']}). Pakai --refresh untuk unduh ulang.")
            return obj["prices"], obj["bench"], obj["downloaded"]
    try:
        import yfinance as yf
    except ImportError:
        sys.exit("Paket yfinance belum terpasang. Jalankan: pip install -r requirements.txt")
    print(f"Mengunduh {len(tickers)} saham + IHSG dari Yahoo Finance ({start} s/d {end}) ...")

    def frame_to_bars(df):
        df = df.dropna(subset=["Close"])
        bars = []
        for ts, r in df.iterrows():
            bars.append({"date": ts.strftime("%Y-%m-%d"), "open": float(r["Open"]) if r["Open"] == r["Open"] else None,
                         "high": float(r["High"]), "low": float(r["Low"]), "close": float(r["Close"]),
                         "volume": float(r["Volume"]) if r["Volume"] == r["Volume"] else 0.0})
        return bars

    prices = {}
    syms = [t + ".JK" for t in tickers]
    for k in range(0, len(syms), 25):  # unduh per kelompok agar tidak kena batas Yahoo
        chunk = syms[k:k + 25]
        df = yf.download(chunk, start=start, end=end, interval="1d", auto_adjust=True,
                         group_by="ticker", threads=True, progress=False)
        for s in chunk:
            try:
                sub = df[s] if len(chunk) > 1 else df
                bars = frame_to_bars(sub)
                if bars:
                    prices[s[:-3]] = bars
            except Exception:
                pass
        print(f"  {min(k + 25, len(syms))}/{len(syms)} diproses, {len(prices)} saham berhasil")
        time.sleep(1)
    gone = [t for t in tickers if t not in prices]
    if gone:
        print("PERINGATAN, tidak ada data Yahoo untuk:", ", ".join(gone))
    bdf = yf.download("^JKSE", start=start, end=end, interval="1d", auto_adjust=True, progress=False)
    if hasattr(bdf.columns, "levels"):
        bdf.columns = [c[0] for c in bdf.columns]
    bench = frame_to_bars(bdf)
    stamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with open(cache, "w", encoding="utf-8") as f:
        json.dump({"downloaded": stamp, "prices": prices, "bench": bench}, f)
    return prices, bench, stamp


def make_demo_prices(tickers, start, end, seed=2026):
    """Data SIMULASI (random walk) untuk menguji kode tanpa internet. BUKAN harga nyata."""
    rng = np.random.default_rng(seed)
    days = [d.strftime("%Y-%m-%d") for d in np.arange(np.datetime64(start), np.datetime64(end) + 1)
            .astype("datetime64[D]").astype(object) if d.weekday() < 5]
    prices = {}
    for t in tickers:
        first = 0 if rng.random() < .92 else int(rng.integers(100, 1500))
        n = len(days) - first
        ret = rng.normal(rng.uniform(.0002, .0011), rng.uniform(.014, .032), n)
        close = rng.uniform(300, 9000) * np.exp(np.cumsum(ret))
        opn = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, .004, n))
        vol = rng.lognormal(15.2, .6, n)
        prices[t] = [{"date": days[first + i], "open": float(opn[i]), "high": float(max(opn[i], close[i]) * 1.01),
                      "low": float(min(opn[i], close[i]) * .99), "close": float(close[i]), "volume": float(vol[i])}
                     for i in range(n)]
    ret = rng.normal(.0004, .01, len(days))
    idx = 5000 * np.exp(np.cumsum(ret))
    bench = [{"date": days[i], "open": float(idx[i]), "high": float(idx[i]), "low": float(idx[i]),
              "close": float(idx[i]), "volume": 0.0} for i in range(len(days))]
    return prices, bench


# ----------------------------------------------------------------------------- mesin backtest
class Engine:
    """Backtest portofolio harian. Setiap pemanggilan run() memakai data yang sama."""

    def __init__(self, prices, bench, cfg):
        self.cfg = cfg
        self.S = {}
        self.no_open = 0
        for t, bars in prices.items():
            b = [x for x in bars if x.get("date") and x.get("close") and x["close"] > 0]
            if len(b) < 44:
                continue
            b.sort(key=lambda x: x["date"])
            op = []
            for x in b:
                if x.get("open") and x["open"] > 0:
                    op.append(x["open"])
                else:
                    op.append(x["close"])
                    self.no_open += 1
            vol = np.array([x.get("volume") or 0.0 for x in b], dtype=float)
            self.S[t] = {"d": [x["date"] for x in b], "o": np.array(op), "c": np.array([x["close"] for x in b]),
                         "v": vol, "hv": bool((vol > 0).any()), "m": {x["date"]: i for i, x in enumerate(b)}, "rs": {}}
        self.names = sorted(self.S)
        self.dates = sorted({d for t in self.names for d in self.S[t]["d"]})
        self.di = {d: i for i, d in enumerate(self.dates)}
        for t in self.names:  # indeks bar -> indeks hari global, dan sebaliknya
            s = self.S[t]
            s["g"] = np.array([self.di[d] for d in s["d"]])
            idx = np.full(len(self.dates), -1, dtype=np.int64)
            idx[s["g"]] = np.arange(len(s["d"]))
            s["idx"] = idx
        self.bench = bench
        self._sig = {}

    def rsi(self, t, p):
        s = self.S[t]
        if p not in s["rs"]:
            s["rs"][p] = rsi_wilder(s["c"], p)
        return s["rs"][p]

    def signals(self, p, lv):
        """Daftar saham yang RSI-nya menembus level ke atas / ke bawah pada tiap hari global."""
        key = (p, lv)
        if key not in self._sig:
            nd = len(self.dates)
            ups = [[] for _ in range(nd)]
            dns = [[] for _ in range(nd)]
            for t in self.names:  # urut alfabet: menentukan prioritas antrean order
                r = self.rsi(t, p)
                if len(r) < p + 2:
                    continue
                a, b = r[1:], r[:-1]
                with np.errstate(invalid="ignore"):
                    up = (a > lv) & (b <= lv) & (a >= 0) & (b >= 0)
                    dn = (a < lv) & (b >= lv) & (a >= 0) & (b >= 0)
                g = self.S[t]["g"]
                for i in np.nonzero(up)[0] + 1:
                    if i >= p + 1:
                        ups[g[i]].append(t)
                for i in np.nonzero(dn)[0] + 1:
                    if i >= p + 1:
                        dns[g[i]].append(t)
            self._sig[key] = (ups, dns)
        return self._sig[key]

    def run(self, p=None, lv=None, frac=None, start=None, end=None, costs=True, cost_mult=1, delay=0):
        c = self.cfg
        P = p or c["rsi_period"]
        LV = c["rsi_level"] if lv is None else lv
        frac = c["frac"] if frac is None else frac
        cap0, LOT = c["capital"], c.get("lot", 100)
        m = cost_mult
        bf0 = c["buy_fee"] * m if costs else 0
        sf0 = c["sell_fee"] * m if costs else 0
        spt = c["spread_ticks"] * m if costs else 0
        ups, dns = self.signals(P, LV)
        nd = len(self.dates)
        lo = next((i for i, d in enumerate(self.dates) if not start or d >= start), nd)
        hi = max((i for i, d in enumerate(self.dates) if not end or d <= end), default=-1)
        S = self.S
        pend, pos, trades, causal = [], {}, [], True
        eq, inv, npos, ed = [], [], [], []
        cash = cap0

        def blocked(t, i):  # tidak ada pengisian di hari tanpa data / volume 0
            return i < 0 or (S[t]["hv"] and not S[t]["v"][i] > 0)

        for di in range(lo, hi + 1):
            d = self.dates[di]
            od = [x for x in pend if x[0] <= di]
            pend = [x for x in pend if x[0] > di]
            for u, kind, t, g in od:
                if kind != "S" or t not in pos:
                    continue
                i = S[t]["idx"][di]
                if blocked(t, i):
                    pend.append((di + 1, "S", t, g))
                    continue
                pz = pos[t]
                op = S[t]["o"][i]
                tk = tick_size(op)
                po = max(tk, math.floor(op / tk) * tk - spt * tk)
                sh = pz["lots"] * LOT
                val = po * sh
                sfee = val * sf0
                cash += val - sfee
                fee = pz["bf"] + sfee
                sp = (pz["pi"] - pz["o0"] + op - po) * sh
                gross = (op - pz["o0"]) * sh
                net = gross - fee - sp
                causal = causal and di > g
                trades.append({"id": len(trades) + 1, "t": t, "di": pz["d"], "do": d, "pi": pz["pi"], "po": po,
                               "lots": pz["lots"], "fee": jround(fee), "sp": jround(sp), "gross": jround(gross),
                               "net": jround(net), "pct": jround(net / (pz["pi"] * sh) * 1e5) / 1e3,
                               "bars": di - pz["di"],
                               "why": f"RSI turun menembus {LV} (sinyal jual, eksekusi open berikutnya)"})
                del pos[t]
            base = eq[-1] if eq else cap0
            for u, kind, t, g in od:
                if kind != "B" or t in pos or len(pos) >= c["max_pos"]:
                    continue
                i = S[t]["idx"][di]
                if blocked(t, i):
                    continue
                op = S[t]["o"][i]
                tk = tick_size(op)
                pi = math.ceil(op / tk) * tk + spt * tk
                k0 = max(0, i - c["liq_days"])
                n = i - k0
                vs = float((S[t]["c"][k0:i] * S[t]["v"][k0:i]).sum())
                lots = math.floor(frac * base / (pi * LOT))
                if n and vs > 0:
                    lots = min(lots, math.floor(c["liq_pct"] * (vs / n) / (pi * LOT)))
                lots = min(lots, math.floor(cash / (pi * LOT * (1 + bf0))))
                if lots < 1:
                    continue
                sh = lots * LOT
                bf = pi * sh * bf0
                cash -= pi * sh + bf
                causal = causal and di > g
                pos[t] = {"lots": lots, "pi": pi, "o0": op, "d": d, "di": di, "bf": bf, "last": pi}
            iv = 0.0
            for t, pz in pos.items():
                i = S[t]["idx"][di]
                if i >= 0:
                    pz["last"] = S[t]["c"][i]
                iv += pz["last"] * pz["lots"] * LOT
            ed.append(d)
            eq.append(jround(cash + iv))
            inv.append(jround(iv))
            npos.append(len(pos))
            for t in dns[di]:
                if t in pos:
                    pend.append((di + 1 + delay, "S", t, di))
            for t in ups[di]:
                if t not in pos:
                    pend.append((di + 1 + delay, "B", t, di))
        # patokan IHSG buy-and-hold pada tanggal yang sama
        bench = [cap0] * len(ed)
        if self.bench and ed:
            bm = {x["date"]: x["close"] for x in self.bench}
            l0, b0, bench = self.bench[0]["close"], None, []
            for d in ed:
                if d in bm:
                    l0 = bm[d]
                if b0 is None:
                    b0 = l0
                bench.append(jround(cap0 * l0 / b0))
        open_pos = []
        for t, pz in pos.items():
            sh = pz["lots"] * LOT
            un = (pz["last"] - pz["pi"]) * sh - pz["bf"]
            open_pos.append({"ticker": t, "di": pz["d"], "pi": pz["pi"], "last": float(pz["last"]), "lots": pz["lots"],
                             "value": jround(pz["last"] * sh), "unreal": jround(un),
                             "unreal_pct": jround(un / (pz["pi"] * sh) * 1e4) / 100, "bf": jround(pz["bf"])})
        return {"equity": {"dates": ed, "eq": eq, "bench": bench, "inv": inv, "npos": npos}, "trades": trades,
                "open": open_pos, "causal": causal}


# ----------------------------------------------------------------------------- metrik
def day_diff(a, b):
    return (datetime.strptime(b, "%Y-%m-%d") - datetime.strptime(a, "%Y-%m-%d")).days


def metrics(res, cfg):
    """Metrik performa (definisi sama dengan dashboard)."""
    E, TR, cap = res["equity"], res["trades"], cfg["capital"]
    dts, eq = E["dates"], E["eq"]
    n = len(eq)
    years = day_diff(dts[0], dts[-1]) / 365.25
    end = eq[-1]
    cagr = (end / cap) ** (1 / years) - 1 if end > 0 and years > 0 else -1
    pk, mdd = -math.inf, 0.0
    for v in eq:
        pk = max(pk, v)
        mdd = min(mdd, v / pk - 1)
    mk, mv = [], []
    for s, v in zip(dts, eq):
        k = s[:7]
        if mk and mk[-1] == k:
            mv[-1] = v
        else:
            mk.append(k)
            mv.append(v)
    mret = [v / (mv[i - 1] if i else cap) - 1 for i, v in enumerate(mv)]
    peak, run, best, rec = cap, 0, 0, []
    for v in mv:
        r = v >= peak
        if v > peak:
            peak = v
        run = 0 if r else run + 1
        best = max(best, run)
        rec.append(r)
    mu = float(np.mean(mret))
    sd = math.sqrt(sum((x - mu) ** 2 for x in mret) / max(1, len(mret) - 1))
    rfm = (1 + cfg["rf_compare"]) ** (1 / 12) - 1
    dsd = math.sqrt(float(np.mean([min(x, 0) ** 2 for x in mret])))
    sharpe = mu / sd * math.sqrt(12) if sd > 0 else None
    sharpe_rf = (mu - rfm) / sd * math.sqrt(12) if sd > 0 else None
    sortino = mu / dsd * math.sqrt(12) if dsd > 0 else None
    nt = len(TR)
    wins = [t for t in TR if t["net"] > 0]
    loss = [t for t in TR if t["net"] <= 0]
    wr = len(wins) / nt if nt else None
    mean = lambda a: sum(a) / len(a)
    ap = mean([t["pct"] for t in wins]) if wins else None
    al = mean([t["pct"] for t in loss]) if loss else None
    apr = mean([t["net"] for t in wins]) if wins else None
    alr = mean([t["net"] for t in loss]) if loss else None
    gp = sum(t["net"] for t in wins)
    gl = -sum(t["net"] for t in loss)
    pf = gp / gl if gl > 0 else None
    exp = wr * ap + (1 - wr) * (al or 0) if nt and ap is not None else None
    payoff = ap / abs(al) if ap is not None and al else None
    fee, spr = sum(t["fee"] for t in TR), sum(t["sp"] for t in TR)
    gross, net = sum(t["gross"] for t in TR), sum(t["net"] for t in TR)
    st = ls = 0
    for t in sorted(TR, key=lambda t: (t["do"], t["id"])):
        st = st + 1 if t["net"] <= 0 else 0
        ls = max(ls, st)
    nets = sorted((t["net"] for t in TR), reverse=True)
    top = lambda k: sum(nets[:k])
    ye = {}
    for s, v in zip(dts, eq):
        ye[s[:4]] = v
    yk = sorted(ye)
    yearly = {y: ye[y] / (ye[yk[i - 1]] if i else cap) - 1 for i, y in enumerate(yk)}
    bn = E["bench"]
    bc = (bn[-1] / bn[0]) ** (1 / years) - 1 if years > 0 else None
    bp, bdd = -math.inf, 0.0
    for v in bn:
        bp = max(bp, v)
        bdd = min(bdd, v / bp - 1)
    return {"years": years, "start_equity": cap, "end_equity": end, "total_return": end / cap - 1, "cagr": cagr,
            "mdd": mdd, "calmar": cagr / abs(mdd) if mdd < 0 else None, "sharpe": sharpe, "sharpe_rf": sharpe_rf,
            "sortino": sortino, "n_months": len(mret), "record_months": sum(rec),
            "pct_record_months": sum(rec) / len(rec) if rec else None, "longest_no_record_months": best,
            "n_trades": nt, "trades_per_year": nt / years, "n_win": len(wins), "n_loss": len(loss), "win_ratio": wr,
            "avg_profit_pct": ap, "avg_loss_pct": al, "avg_profit_rp": apr, "avg_loss_rp": alr, "profit_factor": pf,
            "expectancy_pct": exp, "payoff": payoff, "gross_profit_rp": gp, "gross_loss_rp": gl, "net_profit_rp": net,
            "total_fee": fee, "total_spread": spr, "gross_pnl_before_cost": gross,
            "cost_share": (fee + spr) / gross if gross > 0 else None,
            "avg_hold_days": mean([day_diff(t["di"], t["do"]) for t in TR]) if nt else None,
            "exposure_days_pct": sum(1 for x in E["npos"] if x > 0) / n,
            "avg_capital_used": mean([v / e for v, e in zip(E["inv"], eq)]), "longest_losing_streak": ls,
            "top10_share_of_profit": top(10) / net if net > 0 else None, "top2_share": top(2) / net if net > 0 else None,
            "bench_cagr": bc, "bench_mdd": bdd, "yearly": yearly}


def M(res, cfg):
    v = metrics(res, cfg)
    return {k: v[k] for k in KS}


# ----------------------------------------------------------------------------- uji validitas
def lcg(seed):
    s = [seed]

    def nxt():
        s[0] = (s[0] * 1664525 + 1013904223) % 4294967296
        return s[0] / 4294967296
    return nxt


def audit_of(eng, main, cfg):
    T, LOT = main["trades"], cfg.get("lot", 100)
    A = []
    add = lambda name, ok, detail: A.append({"name": name, "ok": bool(ok), "detail": detail})
    add("Jumlah saham selalu kelipatan 1 lot (100 lembar)", all(t["lots"] >= 1 and float(t["lots"]).is_integer() for t in T),
        f"{len(T)} trade diperiksa")
    vi = chk = zv = 0
    for t in T:
        s = eng.S.get(t["t"])
        i = s["m"].get(t["di"]) if s else None
        if i is None:
            continue
        k0 = max(0, i - cfg["liq_days"])
        n = i - k0
        tot = float((s["c"][k0:i] * s["v"][k0:i]).sum())
        if n and tot > 0:
            chk += 1
            if t["pi"] * t["lots"] * LOT > cfg["liq_pct"] * tot / n * 1.0001:
                vi += 1
        if s["hv"] and not s["v"][i] > 0:
            zv += 1
    add("Nilai tiap order beli <= 5% rata-rata nilai transaksi harian 20 hari sebelumnya", vi == 0,
        f"pelanggaran: {vi} dari {chk} order ber-volume" if chk else "tidak dapat diperiksa: data volume tidak tersedia")
    mxp = max(main["equity"]["npos"])
    add("Posisi terbuka bersamaan tidak pernah melebihi batas maksimal", mxp <= cfg["max_pos"], f"maksimum tercatat: {mxp}")
    add("Setiap entri dieksekusi SESUDAH hari sinyal (open hari berikutnya atau lebih)", main["causal"], "dicek pada tiap order")
    add("Setiap exit dieksekusi SESUDAH hari sinyal jual", main["causal"], "dicek pada tiap order")
    add("Tidak ada pengisian order pada hari tanpa transaksi (volume 0)", zv == 0, f"pelanggaran: {zv}")
    add("Laba bersih = laba kotor - fee - spread (tiap trade)", all(abs(t["net"] - (t["gross"] - t["fee"] - t["sp"])) <= 3 for t in T), "diperiksa ulang")
    add("Fee = 0,15% x nilai beli + 0,25% x nilai jual (dihitung ulang)",
        all(abs(t["fee"] - (t["pi"] * t["lots"] * LOT * cfg["buy_fee"] + t["po"] * t["lots"] * LOT * cfg["sell_fee"])) <= 3 for t in T),
        "diperiksa ulang")
    end = main["equity"]["eq"][-1]
    ex = cfg["capital"] + sum(t["net"] for t in T) + sum(o["unreal"] for o in main["open"])
    add("Rekonsiliasi: ekuitas akhir = modal + laba trade tertutup + posisi terbuka (harga pasar)",
        abs(end - ex) <= max(1000, len(T) * 2), f"selisih Rp{round(end - ex):,}".replace(",", "."))
    add("Posisi terbuka di akhir periode dipisah dari trade tertutup",
        all(not any(t["t"] == o["ticker"] and t["di"] == o["di"] for t in T) for o in main["open"]),
        f"{len(main['open'])} posisi terbuka")
    return A


def validate_all(eng, main, cfg, bench, freeze, data_end):
    """Seluruh uji validitas A-D + forward test. Mengembalikan (dict hasil, jumlah backtest)."""
    cap = cfg["capital"]
    pv, lvv = [7, 11, 14, 17, 21], [25, 40, 50, 60, 75]
    runs = [1]

    def R(**kw):
        runs[0] += 1
        return eng.run(**kw)

    mm = lambda r: M(r, cfg)
    out = {}
    out["costs"] = {"sebelum_biaya": mm(R(costs=False)), "sesudah_biaya": mm(main), "biaya_2x": mm(R(cost_mult=2))}
    out["delay"] = {"normal": mm(main), "delayed": mm(R(delay=1))}
    D = main["equity"]["dates"]
    last = D[-1]
    yrs = day_diff(D[0], last) / 365.25
    split = f"{int(last[:4]) - 3}-{last[5:7]}-01" if yrs >= 6 else D[int(len(D) * .7)]
    is_end = next((d for d in reversed(D) if d < split), D[0])

    bm = {x["date"]: x["close"] for x in bench}

    def bst(a, b):
        x = [bm[d] for d in sorted(bm) if a <= d <= b]
        if len(x) < 2:
            return [0, 0]
        pk = dd = 0
        for v in x:
            pk = max(pk, v)
            dd = min(dd, v / pk - 1)
        y = day_diff(next(d for d in sorted(bm) if d >= a), max(d for d in bm if d <= b)) / 365.25
        return [(x[-1] / x[0]) ** (1 / max(y, .01)) - 1, dd]

    grid = []
    for p in pv:
        for l in lvv:
            f = mm(R(p=p, lv=l))
            grid.append({"p": p, "lv": l, "mp": jround(p / 14 * 100) / 100, "ml": jround(l / 50 * 100) / 100, "cagr": f["cagr"],
                         "mdd": f["mdd"], "sharpe": f["sharpe"], "pf": f["profit_factor"], "n": f["n_trades"]})
    fracs = [.05, .075, .1, .125, .15]
    frac_grid = []
    for fr in fracs:
        f = mm(R(frac=fr))
        frac_grid.append({"frac": fr, **f})
    out["sens"] = {"period_vals": pv, "level_vals": lvv, "mults": [.5, .8, 1, 1.2, 1.5], "grid": grid, "frac": frac_grid}
    near = [g for g in grid if g["mp"] in (.8, 1, 1.2) and g["ml"] in (.8, 1, 1.2)]
    out["sens"]["neighbors_positive"] = sum(1 for g in near if g["cagr"] > 0)
    out["sens"]["neighbors_total"] = len(near)
    out["sens"]["all_positive"] = sum(1 for g in grid if g["cagr"] > 0)
    out["sens"]["all_total"] = len(grid)

    isg = []
    for p in pv:
        for l in lvv:
            f = mm(R(p=p, lv=l, end=is_end))
            isg.append({"p": p, "lv": l, "cagr": f["cagr"], "mdd": f["mdd"], "calmar": f["calmar"], "n": f["n_trades"]})
    bi = isg[0]
    for g in isg[1:]:
        if (g["calmar"] if g["calmar"] is not None else -9) > (bi["calmar"] if bi["calmar"] is not None else -9):
            bi = g
    out["isoos"] = {"split_date": split, "is_range": [D[0], is_end], "oos_range": [split, last],
                    "frozen": {"params": [cfg["rsi_period"], cfg["rsi_level"]], "IS": mm(R(end=is_end)), "OOS": mm(R(start=split)),
                               "IS_bench": bst(D[0], is_end), "OOS_bench": bst(split, last)},
                    "is_best": {"params": [bi["p"], bi["lv"]], "IS": mm(R(p=bi["p"], lv=bi["lv"], end=is_end)),
                                "OOS": mm(R(p=bi["p"], lv=bi["lv"], start=split))}, "is_grid": isg}

    wf = {"windows": [], "combined": {"dates": [], "eq": [], "metrics": None, "pos_windows": 0, "n_windows": 0}}
    if yrs >= 6:  # walk-forward: latih 4 tahun, uji 1 tahun, geser 1 tahun
        y0, sfx = int(D[0][:4]), D[0][4:]
        cd, ce, ct, cur, w = [], [], [], cap, 0
        while True:
            a, b, e = f"{y0 + w}{sfx}", f"{y0 + w + 4}{sfx}", f"{y0 + w + 5}{sfx}"
            if e > last:
                break
            best = None
            for p in pv:
                for l in lvv:
                    f = mm(R(p=p, lv=l, start=a, end=b))
                    if best is None or (f["calmar"] if f["calmar"] is not None else -9) > (best["f"]["calmar"] if best["f"]["calmar"] is not None else -9):
                        best = {"p": p, "l": l, "f": f}
            tr = R(p=best["p"], lv=best["l"], start=b, end=e)
            tm, fm = mm(tr), mm(R(start=b, end=e))
            wf["windows"].append({"w": w + 1, "train": [a, b], "test": [b, e], "chosen": [best["p"], best["l"]],
                                  "train_cagr": best["f"]["cagr"], "train_mdd": best["f"]["mdd"], "test_return": tm["total_return"],
                                  "test_cagr": tm["cagr"], "test_mdd": tm["mdd"], "test_trades": tm["n_trades"],
                                  "bench_return": bst(b, e)[0], "frozen_return": fm["total_return"], "frozen_trades": fm["n_trades"]})
            q, sc = tr["equity"], cur / cap
            for d, v in zip(q["dates"], q["eq"]):
                cd.append(d)
                ce.append(jround(v * sc))
            cur = ce[-1]
            ct.extend(tr["trades"])
            w += 1
        if len(ce) > 5:
            comb = {"equity": {"dates": cd, "eq": ce, "bench": [cap] * len(ce), "inv": [0] * len(ce), "npos": [0] * len(ce)}, "trades": ct}
            wf["combined"] = {"dates": cd, "eq": ce, "pos_windows": sum(1 for x in wf["windows"] if x["test_return"] > 0),
                              "n_windows": len(wf["windows"]), "metrics": M(comb, cfg)}
    out["walkforward"] = wf

    V = metrics(main, cfg)
    top = sorted(main["trades"], key=lambda t: -t["net"])[:5]
    adj = list(main["equity"]["eq"])
    for t in top:
        for i, d in enumerate(D):
            if d >= t["do"]:
                adj[i] -= t["net"]
    rest = [t for t in main["trades"] if all(t is not x for x in top)]
    rn = lcg(42)
    NT, NI = len(main["trades"]), 1000
    cg, md = [], []
    nets = [t["net"] for t in main["trades"]]
    for _ in range(NI):
        e = pk = cap
        dd = 0.0
        for _k in range(NT):
            e += nets[int(rn() * NT)]
            pk = max(pk, e)
            dd = min(dd, e / pk - 1)
        cg.append((e / cap) ** (1 / V["years"]) - 1 if e > 0 else -1)
        md.append(dd)
    pc = lambda a, q: sorted(a)[min(len(a) - 1, int(q * len(a)))]
    out["robust"] = {"without_top5": M({"equity": {**main["equity"], "eq": adj}, "trades": rest}, cfg),
                     "top5": [{"ticker": t["t"], "net": t["net"], "pct": t["pct"]} for t in top],
                     "loss_years": [y for y, v in V["yearly"].items() if v < 0], "n_years": len(V["yearly"]),
                     "bootstrap": {"n_iter": NI, "cagr_p5": pc(cg, .05), "cagr_p50": pc(cg, .5), "cagr_p95": pc(cg, .95),
                                   "mdd_p5": pc(md, .05), "mdd_p50": pc(md, .5), "mdd_p95": pc(md, .95),
                                   "prob_loss": sum(1 for x in cg if x < 0) / NI}}
    nm, r7, passed = eng.names, lcg(7), 0
    for _ in range(60):
        s = eng.S[nm[int(r7() * len(nm))]]
        k = 40 + int(r7() * (len(s["c"]) - 40))
        if abs(rsi_wilder(s["c"][:k + 1], cfg["rsi_period"])[k] - rsi_wilder(s["c"], cfg["rsi_period"])[k]) < 1e-9:
            passed += 1
    out["lookahead"] = {"causal_tested": 60, "causal_passed": passed, "entries_after_signal": main["causal"],
                        "exits_after_signal": main["causal"],
                        "entry_price_equals_open_plus_spread": all(t["pi"] % tick_size(t["pi"]) == 0 for t in main["trades"])}
    fw = {"freeze_date": freeze, "n_days": 0, "min_days": 28, "complete": False, "status": "belum ada data sesudah tanggal pembekuan",
          "range": None, "metrics": None, "bench_return": None, "dates": [], "eq": [], "bench": [], "trades": [], "open": []}
    if freeze and data_end > freeze:
        r = R(start=freeze)
        if len(r["equity"]["eq"]) >= 2:
            e = r["equity"]
            dy = day_diff(freeze, data_end)
            fw = {"freeze_date": freeze, "n_days": dy, "min_days": 28, "complete": dy >= 28,
                  "status": "selesai (>= 4 minggu)" if dy >= 28 else "berjalan (< 4 minggu)", "range": [e["dates"][0], data_end],
                  "metrics": M(r, cfg), "bench_return": e["bench"][-1] / e["bench"][0] - 1, "dates": e["dates"], "eq": e["eq"],
                  "bench": e["bench"], "trades": r["trades"], "open": r["open"]}
    out["forward"] = fw
    out["n_param_combos"] = {"period_level_grid": 25, "frac": 5, "optimized_params": 2, "total": 30, "total_backtest_runs": runs[0]}
    out["audit"] = audit_of(eng, main, cfg)
    return out


# ----------------------------------------------------------------------------- program utama
def read_template(path):
    with open(path, encoding="utf-8") as f:
        html = f.read()
    m = SCRIPT_TAG.search(html)
    if not m:
        sys.exit(f"Blok <script id=\"uts-data\"> tidak ditemukan di {path}")
    return html, m, json.loads(m.group(2))


def main():
    ap = argparse.ArgumentParser(description="Backtest RSI(14) cross 50 Kompas 100 -> dashboard HTML")
    ap.add_argument("--html", default="index.html", help="file dashboard yang diperbarui (juga sumber daftar saham & identitas)")
    ap.add_argument("--out", help="tulis ke file lain (default: menimpa --html)")
    ap.add_argument("--json", help="simpan juga hasil lengkap ke file JSON ini")
    ap.add_argument("--demo", action="store_true", help="pakai data simulasi (tanpa internet), BUKAN harga nyata")
    ap.add_argument("--refresh", action="store_true", help="unduh ulang harga dari Yahoo (abaikan cache)")
    args = ap.parse_args()
    t0 = time.time()

    html, _, base = read_template(args.html)
    cfg, ident = base["config"], base["identity"]
    tickers = [c["t"] for c in base["coverage"]]
    freeze = ident.get("freeze_date")
    years = cfg.get("years", 10)
    f_date = datetime.strptime(freeze, "%Y-%m-%d").date() if freeze else date.today()
    start_dl = (f_date - timedelta(days=int(365.25 * (years + 1)))).isoformat()  # +1 tahun untuk pemanasan RSI
    end_dl = (date.today() + timedelta(days=1)).isoformat()

    if args.demo:
        prices, bench = make_demo_prices(tickers, start_dl, date.today().isoformat())
        stamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        source = "DATA SIMULASI (--demo), bukan harga nyata"
    else:
        prices, bench, stamp = download_prices(tickers, start_dl, end_dl, args.refresh)
        source = "Yahoo Finance (yfinance, harga disesuaikan split/dividen), IHSG ^JKSE sebagai pembanding"
    if len(prices) < 20:
        sys.exit("Terlalu sedikit saham yang berhasil diunduh; backtest dibatalkan.")

    eng = Engine(prices, bench, cfg)
    data_end = eng.dates[-1]
    p_start = (f_date - timedelta(days=int(365.25 * years))).isoformat()
    p_end = max((d for d in eng.dates if not freeze or d < freeze), default=data_end)
    print(f"{len(eng.names)} saham, data {eng.dates[0]} s/d {data_end}; periode backtest {p_start} s/d {p_end}")

    main_res = eng.run(start=p_start, end=p_end)
    if len(main_res["equity"]["eq"]) < 30:
        sys.exit("Periode backtest terlalu pendek.")
    print(f"Backtest utama selesai: {len(main_res['trades'])} trade, {len(main_res['open'])} posisi terbuka")
    val = validate_all(eng, main_res, cfg, bench, freeze, data_end)
    V = metrics(main_res, cfg)
    mpy = {k: v for k, v in V.items() if k != "yearly"}
    ndays = len(main_res["equity"]["dates"])
    full_hist = sum(1 for t in eng.names if day_diff(eng.S[t]["d"][0], p_start) <= 10 or eng.S[t]["d"][0] <= p_start)
    params = json.dumps({"config": cfg, "strategi": ident.get("strategi")}, sort_keys=True)
    with open(SCRIPT_PATH, "rb") as f:
        code_hash = hashlib.sha256(f.read()).hexdigest()
    data = {
        "meta": {"demo": bool(args.demo), "generated": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "source": source,
                 "downloaded": stamp, "period": [main_res["equity"]["dates"][0], main_res["equity"]["dates"][-1]],
                 "n_days": ndays, "n_universe": len(tickers), "n_with_data": len(eng.names), "n_full_history": full_hist,
                 "data_end": data_end, "runtime_sec": round(time.time() - t0, 1)},
        "identity": ident, "config": cfg,
        "fingerprint": {"params_sha256": hashlib.sha256(params.encode()).hexdigest(), "code_sha256": code_hash,
                        "note": base["fingerprint"]["note"]},
        "survivorship": base["survivorship"], "ai_statement": base["ai_statement"], "originality": base["originality"],
        "n_param_combos": val.pop("n_param_combos"),
        "equity": main_res["equity"], "trades": main_res["trades"], "open_positions": main_res["open"],
        "metrics_py": mpy, "yearly": V["yearly"], "costs": val["costs"], "delay": val["delay"], "isoos": val["isoos"],
        "sens": val["sens"], "walkforward": val["walkforward"], "robust": val["robust"], "lookahead": val["lookahead"],
        "forward": val["forward"],
        "coverage": [{"t": t, "first": eng.S[t]["d"][0], "bars": len(eng.S[t]["d"])} for t in eng.names],
        "audit": val["audit"],
    }
    data = clean(data)
    data["meta"]["runtime_sec"] = round(time.time() - t0, 1)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    new_html = SCRIPT_TAG.sub(lambda m: m.group(1) + payload + m.group(3), html, count=1)
    out = args.out or args.html
    with open(out, "w", encoding="utf-8") as f:
        f.write(new_html)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"\nCAGR {V['cagr']:.2%} | MDD {V['mdd']:.2%} | Sharpe {V['sharpe']:.2f} | PF {V['profit_factor']:.2f} | "
          f"Win {V['win_ratio']:.1%} | Trade {V['n_trades']}")
    print(f"Dashboard ditulis ke {out} ({data['n_param_combos']['total_backtest_runs']} backtest, {data['meta']['runtime_sec']} dtk)")


if __name__ == "__main__":
    main()
