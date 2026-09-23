"""
FREE PUT SCANNER
Data source: Yahoo Finance via yfinance.
No paid API key required.

This is a research scanner, not an order-execution system.
Gamma/GEX is an estimate derived from the public option chain.
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import yfinance as yf

from config import *

NY = ZoneInfo("America/New_York")
NASDAQ_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

from collections import Counter
FUNNEL = Counter()   # why tickers were dropped
BETAS = {}           # ticker -> 1y daily beta vs SPY, filled by prefilter()


def safe_float(x):
    try:
        if x is None or (isinstance(x, float) and math.isnan(x)):
            return np.nan
        return float(x)
    except Exception:
        return np.nan


def with_retry(fn, *args, **kwargs):
    """Yahoo rate-limits (HTTP 429) and fails randomly; retry with backoff."""
    last = None
    for attempt in range(RETRIES):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise last


def now_ny():
    return datetime.now(NY)


def market_open():
    n = now_ny()
    return n.weekday() < 5 and (9, 30) <= (n.hour, n.minute) < (16, 0)


def last_session_date(ticker_obj=None):
    """Date of the most recent regular session (today after 9:30 on weekdays)."""
    n = now_ny()
    d = n.date()
    if n.weekday() >= 5 or (n.hour, n.minute) < (9, 30):
        d = d - timedelta(days=1)
        while d.weekday() >= 5:
            d = d - timedelta(days=1)
    return d


# ---------------------------------------------------------------------------
# Black-Scholes (vectorised)
# ---------------------------------------------------------------------------

def _norm_cdf(x):
    return 0.5 * (1.0 + np.vectorize(math.erf)(np.asarray(x, dtype=float) / math.sqrt(2.0)))


def _norm_pdf(x):
    x = np.asarray(x, dtype=float)
    return np.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def years_to_expiry(expiration):
    """Time to the 16:00 New York close on expiration day, in years (min 1 hour)."""
    exp = pd.Timestamp(expiration).to_pydatetime().replace(hour=16, minute=0, tzinfo=NY)
    seconds = (exp - now_ny()).total_seconds()
    return max(seconds, 3600.0) / (365.0 * 24 * 3600)


def bs_delta_gamma(spot, strike, T, iv, is_put, r=None):
    """Black-Scholes delta and gamma. Yahoo does not provide Greeks, so we
    derive them from implied volatility. Invalid inputs return NaN."""
    r = RISK_FREE_RATE if r is None else r
    spot = np.asarray(spot, dtype=float)
    strike = np.asarray(strike, dtype=float)
    iv = np.asarray(iv, dtype=float)
    T = np.asarray(T, dtype=float)
    ok = (spot > 0) & (strike > 0) & np.isfinite(iv) & (iv >= MIN_IV) & (iv <= MAX_IV) & (T > 0)
    iv_s = np.where(ok, iv, 1.0)
    k_s = np.where(ok, strike, 1.0)
    sigma_t = iv_s * np.sqrt(T)
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (np.log(spot / k_s) + (r + 0.5 * iv_s ** 2) * T) / sigma_t
        cdf = _norm_cdf(d1)
        delta = np.where(is_put, cdf - 1.0, cdf)
        gamma = _norm_pdf(d1) / (spot * sigma_t)
    return np.where(ok, delta, np.nan), np.where(ok, gamma, np.nan)


# ---------------------------------------------------------------------------
# Price history (with gap repair) and RSI
# ---------------------------------------------------------------------------

def _to_daily_index(df):
    out = df.copy()
    out.index = pd.to_datetime([ts.date() for ts in out.index])
    out.index.name = "Date"
    return out


def _daily_from_hourly(hourly):
    if hourly is None or hourly.empty:
        return pd.DataFrame()
    h = hourly.copy()
    h["d"] = [ts.date() for ts in h.index]
    agg = h.groupby("d").agg(Open=("Open", "first"), High=("High", "max"),
                             Low=("Low", "min"), Close=("Close", "last"),
                             Volume=("Volume", "sum"))
    agg.index = pd.to_datetime(agg.index)
    agg.index.name = "Date"
    return agg


_calendar_cache = {}


def trading_calendar():
    """Sessions the market actually traded, taken from a reference ticker."""
    if "cal" not in _calendar_cache:
        ref = yf.Ticker(CALENDAR_REFERENCE)
        d = with_retry(ref.history, period="2y", interval="1d", auto_adjust=False)
        h = with_retry(ref.history, period=HOURLY_FILL_PERIOD, interval="1h", auto_adjust=False)
        dates = set(_to_daily_index(d).index) | set(_daily_from_hourly(h).index)
        _calendar_cache["cal"] = pd.DatetimeIndex(sorted(dates))
    return _calendar_cache["cal"]


def get_history(t):
    """Daily bars with missing sessions rebuilt from hourly data.

    Uses split-adjusted but NOT dividend-adjusted closes, which is what
    TradingView/brokers use for RSI by default."""
    daily = with_retry(t.history, period="2y", interval="1d", auto_adjust=False)
    if daily.empty:
        return daily, [], []
    daily = _to_daily_index(daily)[["Open", "High", "Low", "Close", "Volume"]]
    daily = daily[~daily.index.duplicated(keep="last")]

    filled = []
    try:
        hourly = with_retry(t.history, period=HOURLY_FILL_PERIOD, interval="1h", auto_adjust=False)
        from_hourly = _daily_from_hourly(hourly)
        missing = from_hourly.index.difference(daily.index)
        if len(missing):
            daily = pd.concat([daily, from_hourly.loc[missing]]).sort_index()
            filled = [d.date().isoformat() for d in missing]
    except Exception:
        pass

    still_missing = []
    try:
        cal = trading_calendar()
        cal = cal[(cal >= daily.index.min()) & (cal <= daily.index.max())]
        still_missing = [d.date().isoformat() for d in cal.difference(daily.index)]
    except Exception:
        pass

    return daily, filled, still_missing


def nasdaq_closes(ticker):
    """Official daily closes from nasdaq.com, used to cross-check Yahoo's RSI."""
    try:
        import requests
        today = now_ny().date()
        resp = requests.get(
            f"https://api.nasdaq.com/api/quote/{ticker}/historical",
            params={"assetclass": "stocks", "fromdate": (today - timedelta(days=730)).isoformat(),
                    "todate": today.isoformat(), "limit": 1000},
            headers=NASDAQ_HEADERS, timeout=20)
        rows = resp.json()["data"]["tradesTable"]["rows"]
        df = pd.DataFrame(rows)
        closes = pd.Series(df["close"].str.replace(r"[$,]", "", regex=True).astype(float).values,
                           index=pd.to_datetime(df["date"]))
        return closes.sort_index()
    except Exception:
        return pd.Series(dtype=float)


def rsi_wilder(close, period=14):
    """Wilder RSI seeded with an SMA of the first `period` changes (TradingView ta.rsi)."""
    c = close.astype(float).values
    out = np.full(len(c), np.nan)
    if len(c) <= period:
        return pd.Series(out, index=close.index)
    diff = np.diff(c)
    gain = np.clip(diff, 0, None)
    loss = np.clip(-diff, 0, None)
    ag = gain[:period].mean()
    al = loss[:period].mean()

    def val(g, l):
        if l == 0:
            return 100.0 if g > 0 else 50.0
        return 100.0 - 100.0 / (1.0 + g / l)

    out[period] = val(ag, al)
    for i in range(period, len(diff)):
        ag = (ag * (period - 1) + gain[i]) / period
        al = (al * (period - 1) + loss[i]) / period
        out[i + 1] = val(ag, al)
    return pd.Series(out, index=close.index)


def pivot_lows(series, left=3, right=3):
    vals = series.values
    pivots = []
    for i in range(left, len(vals) - right):
        window = vals[i-left:i+right+1]
        if np.isfinite(vals[i]) and vals[i] == np.nanmin(window):
            pivots.append(i)
    return pivots


def bullish_rsi_divergence(low, rsi_series):
    """Daily bullish divergence on completed bars:
       price makes a lower pivot low (using the bar Low) while RSI makes a higher low.
    """
    if len(low) < DIVERGENCE_LOOKBACK:
        return False, None

    p = low.tail(DIVERGENCE_LOOKBACK).reset_index(drop=True)
    r = rsi_series.tail(DIVERGENCE_LOOKBACK).reset_index(drop=True)
    dates = low.tail(DIVERGENCE_LOOKBACK).index

    pivots = pivot_lows(p, PIVOT_LEFT, PIVOT_RIGHT)
    valid = [i for i in pivots if np.isfinite(r.iloc[i])]

    if len(valid) < 2:
        return False, None

    # Compare the two most recent pivot lows.
    i1, i2 = valid[-2], valid[-1]
    if p.iloc[i2] < p.iloc[i1] and r.iloc[i2] > r.iloc[i1]:
        return True, (f"{dates[i1].date()} low {p.iloc[i1]:.2f}/RSI {r.iloc[i1]:.1f} -> "
                      f"{dates[i2].date()} low {p.iloc[i2]:.2f}/RSI {r.iloc[i2]:.1f}")
    return False, None


# ---------------------------------------------------------------------------
# Fundamentals (P/E with fallbacks)
# ---------------------------------------------------------------------------

def get_info(t):
    for attempt in range(2):
        try:
            info = t.info or {}
            if info.get("trailingPE") is not None or info.get("shortName"):
                return info
        except Exception:
            pass
        time.sleep(1.5)
    return {}


def ttm_eps_from_statements(t):
    """Sum of the last four quarterly diluted EPS values."""
    try:
        q = t.quarterly_income_stmt
        if q is None or q.empty:
            return np.nan
        for row in ("Diluted EPS", "Basic EPS"):
            if row in q.index:
                vals = pd.to_numeric(q.loc[row], errors="coerce")
                vals = vals[sorted(vals.index, reverse=True)].dropna()
                if len(vals) >= 4:
                    return float(vals.iloc[:4].sum())
    except Exception:
        pass
    return np.nan


def forward_eps_estimate(t):
    try:
        est = t.earnings_estimate
        if est is not None and not est.empty and "avg" in est.columns:
            for period in ("+1y", "0y"):
                if period in est.index:
                    v = safe_float(est.loc[period, "avg"])
                    if np.isfinite(v):
                        return v
    except Exception:
        pass
    return np.nan


def _trim_sentences(text, limit):
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = cut.rfind(". ")
    return cut[:end + 1] if end > 0 else cut.rsplit(" ", 1)[0] + "…"


def get_pe(t, spot):
    info = get_info(t)
    pe = safe_float(info.get("trailingPE"))
    fpe = safe_float(info.get("forwardPE"))
    eps = safe_float(info.get("trailingEps"))
    source = "yahoo" if np.isfinite(pe) else ""

    if not np.isfinite(eps):
        eps = ttm_eps_from_statements(t)
    if not np.isfinite(pe) and np.isfinite(eps):
        if eps > 0:
            pe = spot / eps
            source = "calc precio/EPS TTM"
        else:
            source = "N/A: EPS TTM negativo"
    if not np.isfinite(pe) and not source:
        source = "missing"

    if not np.isfinite(fpe):
        feps = forward_eps_estimate(t)
        if np.isfinite(feps) and feps > 0:
            fpe = spot / feps
    if np.isfinite(fpe) and fpe <= 0:
        fpe = np.nan  # negative forward EPS: P/E is not meaningful

    return {
        "pe": pe, "pe_source": source, "eps_ttm": eps, "forward_pe": fpe,
        "name": info.get("shortName", t.ticker),
        "sector": info.get("sector", ""), "industry": info.get("industry", ""),
        "market_cap": safe_float(info.get("marketCap")),
        "beta_yahoo": safe_float(info.get("beta")),
        "business_summary": _trim_sentences(info.get("longBusinessSummary") or "", 700),
        "website": info.get("website", ""),
    }


# ---------------------------------------------------------------------------
# Gamma exposure
# ---------------------------------------------------------------------------

def chain_frame(chain, expiration):
    T = years_to_expiry(expiration)
    frames = []
    for df, is_put in ((chain.calls, False), (chain.puts, True)):
        d = df.copy()
        for col in ("strike", "bid", "ask", "lastPrice", "volume", "openInterest", "impliedVolatility"):
            d[col] = pd.to_numeric(d.get(col), errors="coerce")
        d["volume"] = d["volume"].fillna(0)
        d["openInterest"] = d["openInterest"].fillna(0)
        d["is_put"] = is_put
        d["expiration"] = expiration
        d["T"] = T
        frames.append(d)
    return pd.concat(frames, ignore_index=True)


def gex_at(spot, strikes, T, iv, oi, is_put):
    """Net GEX in $ per 1% move (calls +, puts -) evaluated at `spot`."""
    _, gamma = bs_delta_gamma(spot, strikes, T, iv, is_put)
    gamma = np.nan_to_num(gamma)
    sign = np.where(is_put, -1.0, 1.0)
    return sign * gamma * oi * CONTRACT_SIZE * spot ** 2 * 0.01


def gamma_levels(opts, spot, prefix=""):
    """Walls, magnet, top strikes, regime and zero-gamma for a set of options."""
    out = {}
    o = opts[(opts["openInterest"] > 0) & opts["impliedVolatility"].between(MIN_IV, MAX_IV)]
    if o.empty:
        return out

    strikes = o["strike"].values
    T = o["T"].values
    iv = o["impliedVolatility"].values
    oi = o["openInterest"].values
    is_put = o["is_put"].values

    o = o.assign(gex=gex_at(spot, strikes, T, iv, oi, is_put))
    lo, hi = spot * (1 - GEX_WINDOW), spot * (1 + GEX_WINDOW)
    w = o[(o["strike"] >= lo) & (o["strike"] <= hi)]
    if w.empty or w["gex"].abs().sum() == 0:
        return out

    calls = w[~w["is_put"]].groupby("strike")["gex"].sum()
    puts = w[w["is_put"]].groupby("strike")["gex"].sum()
    net = w.groupby("strike")["gex"].sum()
    call_oi = w[~w["is_put"]].groupby("strike")["openInterest"].sum()
    put_oi = w[w["is_put"]].groupby("strike")["openInterest"].sum()

    total = float(o["gex"].sum())
    out[prefix + "net_gex_musd"] = total / 1e6
    out[prefix + "gamma_regime"] = "positivo" if total > 0 else "negativo"
    out[prefix + "call_wall"] = float(calls.idxmax()) if not calls.empty else np.nan
    out[prefix + "put_wall"] = float(puts.idxmin()) if not puts.empty else np.nan
    out[prefix + "call_oi_wall"] = float(call_oi.idxmax()) if not call_oi.empty else np.nan
    out[prefix + "put_oi_wall"] = float(put_oi.idxmax()) if not put_oi.empty else np.nan
    out[prefix + "gamma_magnet"] = float(net.abs().idxmax())
    pos = net[net > 0].sort_values(ascending=False).head(GEX_TOP_LEVELS)
    neg = net[net < 0].sort_values().head(GEX_TOP_LEVELS)
    out[prefix + "top_pos_gamma"] = " / ".join(f"{k:g}" for k in pos.index)
    out[prefix + "top_neg_gamma"] = " / ".join(f"{k:g}" for k in neg.index)

    # Supports / ceilings for put selling: strikes with the most put open interest
    # BELOW spot (where put hedging tends to slow a fall) and the most call open
    # interest ABOVE spot. Gamma is evaluated at the strike itself so far strikes
    # are not ignored just because they are out of the money today.
    def ranked(side_puts, below):
        d = w[(w["is_put"] == side_puts) & ((w["strike"] < spot) if below else (w["strike"] > spot))]
        if d.empty:
            return pd.DataFrame(columns=["strike", "oi", "gex_at_strike"])
        g = gex_at(d["strike"].values, d["strike"].values, d["T"].values,
                   d["impliedVolatility"].values, d["openInterest"].values, d["is_put"].values)
        d = d.assign(g=np.abs(g))
        agg = d.groupby("strike").agg(oi=("openInterest", "sum"), gex_at_strike=("g", "sum")).reset_index()
        agg = agg[agg["oi"] > 0]
        # rank by gamma-at-strike, which is OI weighted by how soon it expires
        return agg.sort_values("gex_at_strike", ascending=False).head(GEX_TOP_LEVELS)

    sup = ranked(True, below=True)
    res = ranked(False, below=False)
    for name, df, key in (("support", sup, "soportes"), ("resistance", res, "techos")):
        if df.empty:
            out[prefix + name + "_main"] = np.nan
            out[prefix + name + "_near"] = np.nan
            out[prefix + key] = ""
            continue
        out[prefix + name + "_main"] = float(df.iloc[0]["strike"])
        out[prefix + name + "_near"] = float(df.loc[(df["strike"] - spot).abs().idxmin(), "strike"])
        out[prefix + key] = " / ".join(f"{r.strike:g} ({int(r.oi):,} OI)" for r in df.itertuples())

    # Zero gamma: re-price total GEX over hypothetical spots, find the sign change nearest spot.
    grid = np.linspace(lo, hi, 161)
    totals = np.array([gex_at(s, strikes, T, iv, oi, is_put).sum() for s in grid])
    flips = []
    for i in range(1, len(grid)):
        a, b = totals[i - 1], totals[i]
        if a == 0 or np.sign(a) != np.sign(b):
            x = grid[i - 1] + (grid[i] - grid[i - 1]) * (a / (a - b) if a != b else 0)
            flips.append(float(x))
    if flips:
        out[prefix + "gamma_flip"] = min(flips, key=lambda x: abs(x - spot))
        out[prefix + "gamma_flip_note"] = ""
    else:
        out[prefix + "gamma_flip"] = np.nan
        out[prefix + "gamma_flip_note"] = ("sin cruce en +/-%d%%: siempre %s"
                                           % (GEX_WINDOW * 100, "positivo" if totals.mean() > 0 else "negativo"))
    return out


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

def get_base(ticker):
    t = yf.Ticker(ticker)
    hist, filled, still_missing = get_history(t)
    if hist.empty:
        FUNNEL["sin datos"] += 1
        return None

    close = hist["Close"].dropna()
    spot = float(close.iloc[-1])
    if not (MIN_PRICE <= spot <= MAX_PRICE):
        FUNNEL["precio fuera de rango"] += 1
        return None

    avg_dollar_volume = float((hist["Close"] * hist["Volume"]).tail(21).iloc[:-1].mean())
    if avg_dollar_volume < MIN_AVG_DOLLAR_VOLUME:
        FUNNEL["poco volumen"] += 1
        return None

    r = rsi_wilder(close, RSI_PERIOD)
    n = now_ny()
    live_bar = close.index[-1].date() == n.date() and n.hour < 16
    completed = close.iloc[:-1] if live_bar else close
    rsi_close = r.iloc[-2] if live_bar else r.iloc[-1]

    rsi_used = float(r.iloc[-1]) if RSI_USE_LIVE_BAR else float(rsi_close)
    if not (RSI_MIN <= rsi_used <= RSI_MAX):
        FUNNEL["RSI diario fuera de %g-%g" % (RSI_MIN, RSI_MAX)] += 1
        return None

    pe_info = get_pe(t, spot)
    if not (np.isfinite(pe_info["pe"]) and 0 < pe_info["pe"] <= MAX_PE):
        FUNNEL["P/E no positivo o > %g" % MAX_PE] += 1
        return None

    weekly = close.resample("W-FRI").last().dropna()
    rsi_weekly = float(rsi_wilder(weekly, RSI_PERIOD).iloc[-1])

    # Cross-check against official Nasdaq closes (same last completed session).
    rsi_nasdaq = np.nan
    nq = nasdaq_closes(ticker)
    if not nq.empty:
        nq = nq[nq.index <= completed.index[-1]]
        if len(nq) > RSI_PERIOD * 5 and nq.index[-1] == completed.index[-1]:
            rsi_nasdaq = float(rsi_wilder(nq, RSI_PERIOD).iloc[-1])

    lows = hist["Low"].loc[completed.index]
    div, div_info = bullish_rsi_divergence(lows, r.loc[completed.index])

    base = {
        "ticker": ticker,
        "spot": spot,
        "rsi": float(r.iloc[-1]),
        "rsi_last_close": float(rsi_close),
        "rsi_bar_is_live": live_bar,
        "rsi_nasdaq_last_close": rsi_nasdaq,
        "rsi_check_diff": float(rsi_close) - rsi_nasdaq if np.isfinite(rsi_nasdaq) else np.nan,
        "rsi_weekly": rsi_weekly,
        "beta": BETAS.get(ticker, np.nan),
        "last_close_date": completed.index[-1].date().isoformat(),
        "bullish_divergence": div,
        "divergence_detail": div_info or "",
        "avg_dollar_volume": avg_dollar_volume,
        "bars_filled_from_hourly": ",".join(filled),
        "bars_still_missing": ",".join(still_missing[-5:]),
        "ticker_obj": t,
    }
    base.update(pe_info)
    return base


def days_to_expiration(expiration):
    return (pd.Timestamp(expiration).date() - now_ny().date()).days


def load_chains(t):
    """All expirations up to GEX_MAX_DTE, plus which ones we sell."""
    expirations = with_retry(lambda: t.options)
    chains = {}
    for exp in expirations:
        dte = days_to_expiration(exp)
        if dte < 0 or dte > max(GEX_MAX_DTE, MAX_DTE):
            continue
        try:
            chains[exp] = chain_frame(with_retry(t.option_chain, exp), exp)
        except Exception:
            continue
        time.sleep(REQUEST_PAUSE / 2)
    sell = [e for e in sorted(chains) if MIN_DTE <= days_to_expiration(e) <= MAX_DTE][:WEEKLY_EXPIRATIONS]
    return chains, sell


def option_candidates(chains, sell_exps, spot):
    rows = []
    for exp in sell_exps:
        opts = chains[exp]
        dte = days_to_expiration(exp)
        levels = gamma_levels(opts, spot, prefix="exp_")

        puts = opts[opts["is_put"]].copy()
        delta, gamma = bs_delta_gamma(spot, puts["strike"].values, puts["T"].values,
                                      puts["impliedVolatility"].values, True)
        puts["delta"] = delta
        puts["gamma"] = gamma

        live = market_open()
        puts = puts[puts["strike"] < spot].copy()
        if live:
            # Live two-sided quote required; stale "last" prices inflate yield.
            puts = puts[(puts["bid"] > 0) & (puts["ask"] > 0)].copy()
            puts["premium_est"] = (puts["bid"] + puts["ask"]) / 2
            puts["premium_source"] = "mid"
        else:
            # After the close market makers widen or pull quotes, so use the last
            # trade of the session (only if it traded that day), kept inside bid/ask.
            session = last_session_date()
            traded = pd.to_datetime(puts["lastTradeDate"], utc=True, errors="coerce").dt.tz_convert(NY).dt.date
            puts = puts[(traded == session) & (puts["lastPrice"] > 0)].copy()
            last = puts["lastPrice"]
            hi = puts["ask"].where(puts["ask"] > 0, last)
            lo = puts["bid"].where(puts["bid"] > 0, 0)
            puts["premium_est"] = last.clip(lower=lo, upper=hi)
            puts["premium_source"] = "último precio del día"
        if puts.empty:
            continue
        puts["mid"] = (puts["bid"] + puts["ask"]) / 2
        puts["delta_abs"] = puts["delta"].abs()

        for _, row in puts.iterrows():
            strike = safe_float(row["strike"])
            premium = safe_float(row["premium_est"])
            delta_abs = safe_float(row["delta_abs"])

            if not np.isfinite(strike) or not np.isfinite(premium) or premium <= 0:
                continue
            if not np.isfinite(delta_abs) or not (MIN_DELTA <= delta_abs <= MAX_DELTA):
                continue
            if row["openInterest"] < MIN_OI and row["volume"] < MIN_OPTION_VOLUME:
                continue

            mid = safe_float(row["mid"])
            spread_pct = (row["ask"] - row["bid"]) / mid if mid and mid > 0 else np.nan
            if live and spread_pct > MAX_BID_ASK_SPREAD_PCT and (row["ask"] - row["bid"]) > MAX_BID_ASK_SPREAD_ABS:
                continue

            collateral = strike * CONTRACT_SIZE
            premium_yield = premium * CONTRACT_SIZE / collateral
            annualized = premium_yield * 365 / max(dte, 1)
            weekly_yield = premium_yield * 7 / max(dte, 1)
            if premium_yield < MIN_PREMIUM_YIELD or weekly_yield < MIN_WEEKLY_YIELD:
                continue

            rec = {
                "expiration": exp,
                "dte": dte,
                "strike": strike,
                "premium": premium,
                "premium_source": row["premium_source"],
                "bid": safe_float(row["bid"]),
                "ask": safe_float(row["ask"]),
                "delta": safe_float(row["delta"]),
                "gamma": safe_float(row["gamma"]),
                "iv": safe_float(row["impliedVolatility"]),
                "volume": int(row["volume"]),
                "oi": int(row["openInterest"]),
                "spread_pct": spread_pct,
                "collateral": collateral,
                "premium_yield": premium_yield,
                "weekly_yield": weekly_yield,
                "annualized_yield": annualized,
                "distance_from_spot": (spot - strike) / spot,
            }
            rec.update(levels)
            rows.append(rec)

    return pd.DataFrame(rows)


def strike_map(opts, spot):
    """STRIKE_MAP_COUNT strikes around spot with call/put OI, volume and gamma.

    Floor = strike below spot with the most put gamma (evaluated at that strike);
    ceiling = strike above spot with the most call gamma. Gamma-at-strike is
    open interest weighted by how soon it expires."""
    o = opts[opts["impliedVolatility"].between(MIN_IV, MAX_IV) | (opts["openInterest"] > 0)].copy()
    if o.empty:
        return [], {}
    strikes = np.sort(o["strike"].dropna().unique())
    half = STRIKE_MAP_COUNT // 2
    below = strikes[strikes < spot][-half:]
    above = strikes[strikes >= spot][:STRIKE_MAP_COUNT - len(below)]
    if len(above) < STRIKE_MAP_COUNT - half:
        below = strikes[strikes < spot][-(STRIKE_MAP_COUNT - len(above)):]
    keep = set(below) | set(above)
    o = o[o["strike"].isin(keep)]

    g_strike = gex_at(o["strike"].values, o["strike"].values, o["T"].values,
                      o["impliedVolatility"].values, o["openInterest"].values, o["is_put"].values)
    g_spot = gex_at(spot, o["strike"].values, o["T"].values,
                    o["impliedVolatility"].values, o["openInterest"].values, o["is_put"].values)
    o = o.assign(g_strike=np.abs(g_strike), g_spot=g_spot)

    rows = []
    for k, d in o.groupby("strike"):
        c, p = d[~d["is_put"]], d[d["is_put"]]
        rows.append({
            "strike": float(k),
            "call_oi": int(c["openInterest"].sum()), "put_oi": int(p["openInterest"].sum()),
            "call_vol": int(c["volume"].sum()), "put_vol": int(p["volume"].sum()),
            "call_gamma": float(c["g_strike"].sum()), "put_gamma": float(p["g_strike"].sum()),
            "net_gex": float(d["g_spot"].sum()),
        })
    rows.sort(key=lambda r: r["strike"], reverse=True)

    lv = {}
    bel = sorted([r for r in rows if r["strike"] < spot and r["put_oi"] > 0], key=lambda r: -r["put_gamma"])
    abv = sorted([r for r in rows if r["strike"] > spot and r["call_oi"] > 0], key=lambda r: -r["call_gamma"])
    lv["floor"] = bel[0]["strike"] if bel else np.nan
    lv["floor_2"] = bel[1]["strike"] if len(bel) > 1 else np.nan
    lv["ceiling"] = abv[0]["strike"] if abv else np.nan
    lv["ceiling_2"] = abv[1]["strike"] if len(abv) > 1 else np.nan
    for r in rows:
        tags = []
        if r["strike"] == lv["floor"]: tags.append("PISO")
        if r["strike"] == lv["floor_2"]: tags.append("PISO 2")
        if r["strike"] == lv["ceiling"]: tags.append("TECHO")
        if r["strike"] == lv["ceiling_2"]: tags.append("TECHO 2")
        r["tag"] = " ".join(tags)
    return rows, lv


def big_contracts(opts):
    """Largest individual contracts by open interest and by volume, plus unusual volume."""
    o = opts[(opts["openInterest"] > 0) | (opts["volume"] > 0)].copy()
    if o.empty:
        return {"by_oi": [], "by_volume": [], "unusual": []}
    o["type"] = np.where(o["is_put"], "PUT", "CALL")
    o["dte"] = [days_to_expiration(e) for e in o["expiration"]]

    def pack(df):
        return [{"type": r.type, "expiration": r.expiration, "dte": int(r.dte), "strike": float(r.strike),
                 "oi": int(r.openInterest), "volume": int(r.volume),
                 "last": safe_float(r.lastPrice), "iv": safe_float(r.impliedVolatility)}
                for r in df.itertuples()]

    unusual = o[(o["volume"] >= UNUSUAL_MIN_VOLUME) & (o["volume"] > o["openInterest"])]
    return {
        "by_oi": pack(o.sort_values("openInterest", ascending=False).head(BIG_CONTRACTS_TOP)),
        "by_volume": pack(o.sort_values("volume", ascending=False).head(BIG_CONTRACTS_TOP)),
        "unusual": pack(unusual.sort_values("volume", ascending=False).head(BIG_CONTRACTS_TOP)),
    }


def earnings_days_away(ticker_obj):
    try:
        ed = ticker_obj.get_earnings_dates(limit=12)
        if ed is None or ed.empty:
            return np.nan
        now = pd.Timestamp.now(tz="UTC")
        future = pd.to_datetime(ed.index, utc=True)
        future = future[future >= now]
        if len(future) == 0:
            return np.nan
        return int((future.min().date() - now_ny().date()).days)
    except Exception:
        return np.nan


SUMMARY_KEYS = [
    "ticker", "name", "spot", "rsi", "rsi_last_close", "last_close_date", "rsi_bar_is_live",
    "rsi_nasdaq_last_close", "rsi_check_diff", "rsi_weekly", "beta", "beta_yahoo",
    "bullish_divergence", "divergence_detail", "pe", "pe_source", "eps_ttm", "forward_pe",
    "sector", "industry", "market_cap", "avg_dollar_volume", "business_summary", "website",
    "bars_filled_from_hourly", "bars_still_missing",
]


def scan_ticker(symbol):
    """Returns (summary dict or None, list of put rows)."""
    try:
        base = get_base(symbol)
        if base is None:
            return None, []

        chains, sell_exps = load_chains(base["ticker_obj"])
        spot = base["spot"]
        summary = {k: base.get(k) for k in SUMMARY_KEYS}
        summary["earnings_days"] = earnings_days_away(base["ticker_obj"])
        summary["sell_expirations"] = ",".join(sell_exps)

        if EXCLUDE_EARNINGS_BEFORE_EXPIRY and sell_exps and np.isfinite(summary["earnings_days"]) \
                and summary["earnings_days"] <= days_to_expiration(sell_exps[-1]):
            FUNNEL["earnings antes del vencimiento"] += 1
            return None, []

        if chains:
            all_opts = pd.concat(chains.values(), ignore_index=True)
            all_opts = all_opts[[days_to_expiration(e) <= GEX_MAX_DTE for e in all_opts["expiration"]]]
            summary.update(gamma_levels(all_opts, spot))
            summary["strike_map"], lv = strike_map(all_opts, spot)
            summary.update(lv)
            summary["big_contracts"] = big_contracts(all_opts)

        puts = option_candidates(chains, sell_exps, spot) if sell_exps else pd.DataFrame()
        if puts.empty:
            FUNNEL["sin semanales" if not sell_exps else "prima semanal < %.2f%%" % (MIN_WEEKLY_YIELD * 100)] += 1
            return None, []
        FUNNEL["PASA (tipo AFRM)"] += 1

        earnings = summary["earnings_days"]
        # Earnings before/at expiration or within the safety window are flagged.
        puts["earnings_warning"] = np.isfinite(earnings) & (
            (earnings <= AVOID_EARNINGS_WITHIN_DAYS) | (earnings <= puts["dte"]))

        for k, v in summary.items():
            if k in ("strike_map", "big_contracts"):
                continue
            if k not in puts.columns:
                puts[k] = v

        puts["strike_below_put_wall"] = puts["strike"] < puts["put_wall"]
        # Support used for each put: the one from its own expiration, else all expirations.
        puts["support_for_put"] = summary.get("floor", np.nan)
        puts["strike_below_support"] = puts["strike"] <= puts["support_for_put"]
        puts["cushion_below_support"] = (puts["support_for_put"] - puts["strike"]) / spot
        puts["pct_strike_to_flip"] = (puts["strike"] - puts["gamma_flip"]) / spot

        # Informational ranking only — NOT a recommendation score.
        puts["research_rank"] = (
            puts["weekly_yield"].clip(0, 0.03) * 100
            + puts["bullish_divergence"].astype(int) * 1.0
            + puts["oi"].clip(0, 10000) / 10000
            + puts["strike_below_support"].fillna(False).astype(int) * 0.5
            - puts["earnings_warning"].astype(int) * 2.0
            - puts["spread_pct"].fillna(0).clip(0, 1) * 2
        )
        return summary, puts.to_dict("records")

    except Exception as e:
        FUNNEL["error de datos"] += 1
        print(f"[WARN] {symbol}: {e}")
        return None, []


def fmt(x, nd=2, pct=False):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "missing"
    if pct:
        return f"{x * 100:.{nd}f}%"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def write_markdown(summaries, report, path="daily_put_report.md"):
    lines = [f"# Daily Put Scanner — {now_ny():%Y-%m-%d %H:%M} ET", "",
             "Gamma levels are estimates from the public option chain (all expirations "
             f"<= {GEX_MAX_DTE} DTE, calls +, puts -). Not guaranteed support/resistance.", ""]
    for s in summaries:
        tk = s["ticker"]
        lines += [f"## {tk} — {s.get('name') or tk}  (spot {fmt(s['spot'])})", ""]
        rsi_line = f"- RSI(14) diario: **{fmt(s['rsi'], 1)}**"
        if s.get("rsi_bar_is_live"):
            rsi_line += f" (vela en curso) · cierre {s['last_close_date']}: {fmt(s['rsi_last_close'], 1)}"
        lines.append(rsi_line)
        lines.append(f"- RSI al cierre según Nasdaq (verificación): {fmt(s['rsi_nasdaq_last_close'], 1)}"
                     f" · RSI(14) semanal: {fmt(s['rsi_weekly'], 1)}")
        lines.append(f"- Divergencia alcista RSI: {'SÍ — ' + s['divergence_detail'] if s['bullish_divergence'] else 'no'}")
        lines.append(f"- P/E: {fmt(s['pe'], 1)} ({s['pe_source']}) · EPS TTM {fmt(s['eps_ttm'])}"
                     f" · P/E forward {fmt(s['forward_pe'], 1) if np.isfinite(safe_float(s['forward_pe'])) else 'N/A'}")
        lines.append(f"- Earnings en: {fmt(s['earnings_days'], 0)} días")
        if s.get("bars_filled_from_hourly"):
            lines.append(f"- Velas diarias reconstruidas desde datos por hora: {s['bars_filled_from_hourly']}")
        if s.get("bars_still_missing"):
            lines.append(f"- ⚠ Velas que siguen faltando: {s['bars_still_missing']}")
        flip = fmt(s.get("gamma_flip")) if np.isfinite(safe_float(s.get("gamma_flip"))) else s.get("gamma_flip_note") or "missing"
        lines.append(f"- **Techos (calls, arriba del precio):** {s.get('techos') or 'missing'}")
        lines.append(f"- **Soportes (puts, abajo del precio):** {s.get('soportes') or 'missing'}")
        lines.append(f"- Régimen gamma **{s.get('gamma_regime', 'missing')}** "
                     f"({'movimientos amortiguados' if s.get('gamma_regime') == 'positivo' else 'movimientos amplificados'})"
                     f" · Gamma flip: {flip} · Magnet: {fmt(s.get('gamma_magnet'))}")
        rows = report[report["ticker"] == tk] if not report.empty else report
        if rows is not None and not rows.empty:
            lines += ["", "| Venc. | DTE | Strike | Prima | Delta | IV | OI | Rend. (7d) | Dist. | Soporte venc. | Techo venc. | ¿Strike bajo soporte? | Earnings antes |",
                      "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
            for _, r in rows.iterrows():
                lines.append(
                    f"| {r['expiration']} | {r['dte']} | {r['strike']:g} | {r['premium']:.2f} | {r['delta']:.2f} | "
                    f"{r['iv'] * 100:.0f}% | {r['oi']} | {r['premium_yield'] * 100:.2f}% ({r['weekly_yield'] * 100:.2f}%) | "
                    f"{r['distance_from_spot'] * 100:.1f}% | {r.get('exp_soportes') or 'missing'} | "
                    f"{r.get('exp_techos') or 'missing'} | "
                    f"{'sí' if r['strike_below_support'] else 'NO, arriba'} | {'⚠ sí' if r['earnings_warning'] else 'no'} |")
        else:
            lines += ["", "_Sin puts semanales que pasen los filtros._"]
        lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def nasdaq_universe():
    """All US stocks from the Nasdaq screener inside the price / market-cap range."""
    resp = requests.get("https://api.nasdaq.com/api/screener/stocks",
                        params={"tableonly": "true", "limit": 10000, "download": "true"},
                        headers=NASDAQ_HEADERS, timeout=30)
    d = pd.DataFrame(resp.json()["data"]["rows"])
    d["price"] = pd.to_numeric(d["lastsale"].str.replace(r"[$,]", "", regex=True), errors="coerce")
    d["mcap"] = pd.to_numeric(d["marketCap"], errors="coerce")
    d = d[d["price"].between(MIN_PRICE * 0.9, MAX_PRICE * 1.1) & (d["mcap"] >= MIN_MARKET_CAP)]
    syms = [s.strip().replace("/", "-") for s in d["symbol"] if s and "^" not in s]
    return sorted(set(syms))


def beta_vs(close, bench_returns):
    """Beta of daily returns vs the benchmark over the last BETA_LOOKBACK_DAYS sessions."""
    r = close.pct_change()
    df = pd.concat([r, bench_returns], axis=1, join="inner").dropna().tail(BETA_LOOKBACK_DAYS)
    if len(df) < 120:
        return np.nan
    var = df.iloc[:, 1].var()
    return float(df.iloc[:, 0].cov(df.iloc[:, 1]) / var) if var > 0 else np.nan


def spy_returns():
    spy = yf.download("SPY", period="2y", interval="1d", auto_adjust=False, progress=False)
    close = spy["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    close = _to_daily_index(close.to_frame("Close"))["Close"]
    return close.pct_change().rename("bench")


def prefilter(symbols, chunk=150):
    """Batch pass: price, dollar volume and exact daily RSI (missing sessions
    rebuilt from hourly bars, same as get_history). Only survivors get the
    per-ticker P/E and option-chain work."""
    keep = []
    bench = spy_returns()
    for i in range(0, len(symbols), chunk):
        batch = symbols[i:i + chunk]
        try:
            data = yf.download(batch, period="2y", interval="1d", auto_adjust=False,
                               group_by="ticker", threads=True, progress=False)
            hourly = yf.download(batch, period=HOURLY_FILL_PERIOD, interval="1h", auto_adjust=False,
                                 group_by="ticker", threads=True, progress=False)
        except Exception:
            keep.extend(batch)
            continue
        for sym in batch:
            try:
                df = _to_daily_index(data[sym].dropna(subset=["Close"]))
                if len(df) < 60:
                    continue
                try:
                    fh = _daily_from_hourly(hourly[sym].dropna(subset=["Close"]))
                    miss = fh.index.difference(df.index)
                    if len(miss):
                        df = pd.concat([df, fh.loc[miss]]).sort_index()
                except Exception:
                    pass
                spot = float(df["Close"].iloc[-1])
                dv = float((df["Close"] * df["Volume"]).tail(21).iloc[:-1].mean())
                r = rsi_wilder(df["Close"], RSI_PERIOD)
                n = now_ny()
                live_bar = df.index[-1].date() == n.date() and n.hour < 16
                rsi_used = float(r.iloc[-1] if (RSI_USE_LIVE_BAR or not live_bar) else r.iloc[-2])
                if not (MIN_PRICE <= spot <= MAX_PRICE):
                    FUNNEL["precio fuera de rango"] += 1
                elif dv < MIN_AVG_DOLLAR_VOLUME:
                    FUNNEL["poco volumen"] += 1
                elif not (RSI_MIN - 1 <= rsi_used <= RSI_MAX + 1):
                    FUNNEL["RSI diario fuera de %g-%g" % (RSI_MIN, RSI_MAX)] += 1
                else:
                    beta = beta_vs(df["Close"], bench)
                    BETAS[sym] = beta
                    if not (np.isfinite(beta) and beta >= MIN_BETA):
                        FUNNEL["beta < %g" % MIN_BETA] += 1
                    else:
                        keep.append(sym)
            except Exception:
                continue
        time.sleep(REQUEST_PAUSE)
    return keep


def _clean(v):
    if isinstance(v, (np.floating, float)):
        return None if not np.isfinite(v) else float(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    return v


def write_json(summaries, puts_df, universe_size, prefiltered, path="results.json"):
    import json
    put_cols = ["expiration", "dte", "strike", "premium", "premium_source", "bid", "ask", "delta", "iv", "oi", "volume",
                "spread_pct", "premium_yield", "weekly_yield", "distance_from_spot",
                "strike_below_support", "cushion_below_support", "earnings_warning"]
    companies = []
    for s in summaries:
        c = {k: v for k, v in s.items()}
        if not puts_df.empty:
            p = puts_df[puts_df["ticker"] == s["ticker"]].sort_values("strike", ascending=False)
            c["puts"] = p[[x for x in put_cols if x in p.columns]].to_dict("records")
        else:
            c["puts"] = []
        companies.append(c)
    companies.sort(key=lambda c: max([p["premium_yield"] for p in c["puts"]] or [0]), reverse=True)
    out = {
        "generated_at": now_ny().strftime("%Y-%m-%d %H:%M ET"),
        "filters": {"price": [MIN_PRICE, MAX_PRICE], "rsi": [RSI_MIN, RSI_MAX], "min_beta": MIN_BETA, "max_pe": MAX_PE,
                    "min_weekly_yield": MIN_WEEKLY_YIELD, "delta": [MIN_DELTA, MAX_DELTA],
                    "dte": [MIN_DTE, MAX_DTE], "min_market_cap": MIN_MARKET_CAP,
                    "min_dollar_volume": MIN_AVG_DOLLAR_VOLUME, "gex_max_dte": GEX_MAX_DTE,
                    "strike_map_count": STRIKE_MAP_COUNT},
        "funnel": dict(FUNNEL), "universe_size": universe_size, "prefiltered": prefiltered,
        "companies": companies,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_clean(out), f, ensure_ascii=False, indent=1, default=str)


def main():
    print("=" * 72)
    print("FREE DAILY PUT SCANNER")
    print(now_ny().strftime("%Y-%m-%d %H:%M ET"))
    print("=" * 72)

    universe = list(TICKERS)
    if UNIVERSE == "nasdaq":
        try:
            universe = sorted(set(universe) | set(nasdaq_universe()))
        except Exception as e:
            print(f"[WARN] Nasdaq screener failed ({e}); using TICKERS only")
    print(f"Universe: {len(universe)} stocks. Pre-filtering price / volume / daily RSI / beta...")
    symbols = prefilter(universe)
    print(f"{len(symbols)} pass the pre-filter; checking exact RSI, P/E and weekly puts.\n")

    summaries, all_rows = [], []
    for i, symbol in enumerate(symbols, 1):
        print(f"[{i}/{len(symbols)}] {symbol}")
        summary, rows = scan_ticker(symbol)
        if summary:
            summaries.append(summary)
        all_rows.extend(rows)
        time.sleep(REQUEST_PAUSE)

    if summaries:
        flat = [{k: v for k, v in x.items() if k not in ("strike_map", "big_contracts")} for x in summaries]
        pd.DataFrame(flat).to_csv("ticker_summary.csv", index=False)

    df = pd.DataFrame(all_rows)
    if not df.empty:
        df = df.sort_values(["research_rank", "premium_yield"], ascending=False)
    df.to_csv("put_candidates_full.csv", index=False)

    report = (df.groupby("ticker", group_keys=False).head(PUTS_PER_STOCK)
                .head(TOP_N * PUTS_PER_STOCK).copy()) if not df.empty else df
    report.to_csv("daily_put_report.csv", index=False)
    write_markdown(summaries, report)
    write_json(summaries, df, universe_size=len(universe), prefiltered=len(symbols))

    print("\n" + "=" * 72)
    print("TICKERS: RSI / P/E / GAMMA LEVELS")
    print("=" * 72)
    if summaries:
        s = pd.DataFrame(summaries)
        cols = ["ticker", "spot", "beta", "rsi", "rsi_last_close", "rsi_nasdaq_last_close", "rsi_weekly", "bullish_divergence", "pe", "forward_pe",
                "earnings_days", "gamma_regime", "gamma_flip", "support_near", "support_main", "resistance_near", "resistance_main"]
        print(s[[c for c in cols if c in s.columns]].to_string(index=False, float_format=lambda x: f"{x:.2f}"))

    print("\n" + "=" * 72)
    print("TOP WEEKLY PUT CANDIDATES")
    print("=" * 72)
    if report.empty:
        print("No candidates found. Try relaxing filters in config.py.")
    else:
        cols = ["ticker", "spot", "expiration", "dte", "strike", "premium", "delta", "iv", "oi",
                "premium_yield", "weekly_yield", "distance_from_spot", "rsi", "pe", "support_for_put", "strike_below_support", "exp_resistance_near",
                "earnings_warning"]
        print(report[cols].to_string(index=False, float_format=lambda x: f"{x:.3f}"))

    print("\nFunnel (por qué se descartaron):")
    for k, v in FUNNEL.most_common():
        print(f"  {k}: {v}")

    print("\nSaved:")
    for f in ("ticker_summary.csv", "put_candidates_full.csv", "daily_put_report.csv", "daily_put_report.md"):
        print("  " + f)


if __name__ == "__main__":
    main()
