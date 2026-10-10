"""
BTC-USD LEAPS 策略回测
策略: BTC 当日跌 > 1% 买入, 440天 LEAPS Call delta~0.6, 110% 止盈, 50天止损

BTC 关键特征:
- 波动率极高 (60-80% 年化)
- 长期强趋势
- 没有 LEAPS 期权, 用模拟
"""
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from scipy.stats import norm


# ============================================================
# BTC 关键节点 (用于生成模拟)
# ============================================================
BTC_MILESTONES = [
    ("2014-01-01", 800.0),     # 起点
    ("2014-12-31", 320.0),     # 年底
    ("2015-12-31", 430.0),
    ("2016-12-31", 960.0),
    ("2017-12-17", 19760.0),   # 历史峰值 1
    ("2018-12-15", 3200.0),    # 2018 底
    ("2019-12-31", 7200.0),
    ("2020-03-13", 4900.0),    # COVID 崩
    ("2020-12-31", 28900.0),
    ("2021-11-10", 69000.0),   # 历史峰值 2
    ("2022-11-21", 15800.0),   # FTX 崩
    ("2023-12-31", 42200.0),
    ("2024-12-31", 100000.0),  # 当前
]

# BTC 典型参数
BTC_DAILY_VOL = 0.60 / np.sqrt(252)  # 60% 年化波动
BTC_ANNUAL_DRIFT = 0.48  # 实际 BTC 10年年化 (800→100000, ln(125)/10 = 48%)

# ============================================================
# LEAPS 模拟 (比特币没有 LEAPS, 用 BS 模拟)
# ============================================================
def bs_call(S, K, T, r, sigma):
    if T <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (np.log(S/K) + (r + sigma**2/2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    return S * norm.cdf(d1) - K * np.exp(-r*T) * norm.cdf(d2)

def bs_delta(S, K, T, r, sigma):
    if T <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (np.log(S/K) + (r + sigma**2/2) * T) / (sigma * np.sqrt(T))
    return norm.cdf(d1)

# ============================================================
# 生成 BTC 模拟数据 (匹配关键节点)
# ============================================================
def generate_btc_data(start="2014-01-01", end="2024-12-31", seed=42):
    """生成 BTC 模拟日数据, 匹配关键节点"""
    np.random.seed(seed)
    dates = pd.date_range(start, end, freq='B')  # 工作日
    n = len(dates)

    S0 = 800.0
    SN_target = 100000.0  # 实际 2024 末 ~$100,000

    # 精确计算 drift
    total_log_return = np.log(SN_target / S0)
    daily_drift = total_log_return / n

    # BTC 波动率随时间变化
    log_returns = []
    for i in range(n):
        progress = i / n
        vol = BTC_DAILY_VOL * (0.6 + 0.4 * progress)
        r = np.random.normal(daily_drift, vol)
        log_returns.append(r)

    log_returns[0] = 0
    log_prices = np.cumsum(log_returns)
    prices = S0 * np.exp(log_prices)

    df = pd.DataFrame({
        'Date': dates,
        'Close': prices,
    })

    # 缩放到匹配关键节点
    for i in range(1, len(BTC_MILESTONES)):
        ms_date = pd.to_datetime(BTC_MILESTONES[i][0])
        ms_price = BTC_MILESTONES[i][1]
        idx = df[df['Date'] >= ms_date].index[0] if any(df['Date'] >= ms_date) else len(df)-1
        if idx > 0:
            current_at_idx = df.loc[idx, 'Close']
            if current_at_idx > 0:
                scale = ms_price / current_at_idx
                # 从 idx 到下一个 milestone 缩放
                next_ms = pd.to_datetime(BTC_MILESTONES[i+1][0]) if i+1 < len(BTC_MILESTONES) else df['Date'].iloc[-1]
                mask = (df['Date'] >= ms_date) & (df['Date'] < next_ms)
                df.loc[mask, 'Close'] *= scale

    # 重新缩放最后一段到目标终值
    last_mask = df['Date'] >= pd.to_datetime(BTC_MILESTONES[-1][0])
    if last_mask.any():
        current_end = df.loc[last_mask, 'Close'].iloc[-1]
        if current_end > 0:
            scale_end = SN_target / current_end
            df.loc[last_mask, 'Close'] *= scale_end

    return df

# ============================================================
# 牛熊分界 (25 日均线)
# ============================================================
def add_market_regime(df, ma_period=25):
    df = df.copy()
    df['MA25'] = df['Close'].rolling(ma_period).mean()
    df['Regime'] = 'bear'
    df.loc[df['Close'] > df['MA25'], 'Regime'] = 'bull'
    return df

# ============================================================
# 主回测 (含滑点+手续费)
# ============================================================

# BTC 期权参数
BTC_SLIPPAGE_BUY = 0.02     # BTC 流动性比 SOXX 差, 2% 滑点
BTC_SLIPPAGE_SELL = 0.02
BTC_COMMISSION = 5.0         # $5/手
BTC_IV = 0.80                # 80% IV (BTC 实际波动率 60-100%)


def backtest(df, initial_cash=100000,
             drop_threshold=0.01,    # 1% 跌幅
             position_pct=0.20,      # 20% 单笔
             take_profit=2.1,        # +110%
             expiry_days=440,         # 440天
             stop_loss_days=50,       # 50天前止损
             max_pos_bear=500_000,    # 熊市上限
             max_pos_bull=1_000_000,  # 牛市上限
             strike_pct=1.0,          # strike/spot 比例 (1.0 = ATM)
             iv=0.80,                 # 隐含波动率
             r=0.04,                  # 无风险利率
             use_slippage=True,
             use_commission=True):
    """可调参数的主回测"""
    cash = initial_cash
    positions = []
    trades = []
    equity_curve = []
    total_commission = 0

    for i, row in df.iterrows():
        date = row['Date']
        price = row['Close']
        prev_close = df.loc[i-1, 'Close'] if i > 0 else price
        regime = row.get('Regime', 'bear')

        max_pos = max_pos_bull if regime == 'bull' else max_pos_bear
        total_cost = sum(p['cost_basis'] for p in positions)
        if total_cost > max_pos:
            if positions:
                old = positions.pop(0)
                sell_value = old['cost_basis'] * 2 * (1 - BTC_SLIPPAGE_SELL)
                commission = old['qty'] * BTC_COMMISSION if use_commission else 0
                cash += sell_value - commission
                total_commission += commission
                trades.append({'date': date, 'action': 'cap_sell', 'pnl': sell_value - commission - old['cost_basis']})

        # 检查止盈/止损
        for p in list(positions):
            T = (p['expiry_date'] - date).days / 365.0
            if T <= 0:
                intrinsic = max(0, price - p['strike'])
                sell_value = intrinsic * p['qty'] * 100 * (1 - BTC_SLIPPAGE_SELL)
                commission = p['qty'] * BTC_COMMISSION if use_commission else 0
                cash += sell_value - commission
                pnl = (intrinsic * p['qty'] * 100) - p['cost_basis'] - commission
                trades.append({'date': date, 'action': 'expire', 'pnl': pnl})
                positions.remove(p)
                continue

            current_premium = bs_call(price, p['strike'], T, r, iv)
            current_value = current_premium * p['qty'] * 100

            # 止盈
            if current_value >= p['cost_basis'] * take_profit:
                sell_value = current_value * (1 - BTC_SLIPPAGE_SELL)
                commission = p['qty'] * BTC_COMMISSION if use_commission else 0
                cash += sell_value - commission
                pnl = sell_value - p['cost_basis'] - commission
                trades.append({'date': date, 'action': 'take_profit', 'pnl': pnl, 'return': pnl/p['cost_basis']})
                positions.remove(p)
                continue

            # 止损 (到期前 N 天)
            if T * 365 < stop_loss_days:
                sell_value = current_value * (1 - BTC_SLIPPAGE_SELL)
                commission = p['qty'] * BTC_COMMISSION if use_commission else 0
                cash += sell_value - commission
                pnl = sell_value - p['cost_basis'] - commission
                trades.append({'date': date, 'action': 'stop_loss', 'pnl': pnl})
                positions.remove(p)

        # 入场
        daily_return = (price - prev_close) / prev_close if prev_close > 0 else 0

        if daily_return < -drop_threshold:
            total_equity = cash + sum(
                bs_call(price, p['strike'],
                        max(0.01, (p['expiry_date']-date).days/365),
                        r, iv) * p['qty'] * 100
                for p in positions
            )
            budget = min(total_equity * position_pct, cash, max_pos * position_pct)

            if budget > 1000:
                T_years = expiry_days / 365.0
                expiry_date = date + timedelta(days=expiry_days)

                # Strike (按比例)
                strike = price * strike_pct

                # 圆整 (BTC 通常 $500-1000 间隔)
                if price > 10000:
                    strike = round(strike / 500) * 500
                elif price > 1000:
                    strike = round(strike / 100) * 100
                else:
                    strike = round(strike / 10) * 10

                premium = bs_call(price, strike, T_years, r, iv)
                if premium > 0:
                    buy_premium = premium * (1 + BTC_SLIPPAGE_BUY) if use_slippage else premium
                    contract_cost = buy_premium * 100 + (BTC_COMMISSION if use_commission else 0)
                    qty = int(budget / contract_cost)
                    if qty > 0:
                        cost = qty * contract_cost
                        cash -= cost
                        total_commission += qty * (BTC_COMMISSION if use_commission else 0)
                        positions.append({
                            'strike': strike,
                            'premium': buy_premium,
                            'qty': qty,
                            'entry_date': date,
                            'expiry_date': expiry_date,
                            'cost_basis': cost,
                        })
                        trades.append({
                            'date': date,
                            'action': 'buy',
                            'strike': strike,
                            'premium': buy_premium,
                            'qty': qty,
                            'cost': cost,
                            'spot': price,
                        })

        total_equity = cash + sum(
            bs_call(price, p['strike'],
                    max(0.01, (p['expiry_date']-date).days/365),
                    r, iv) * p['qty'] * 100
            for p in positions
        )
        equity_curve.append({'date': date, 'equity': total_equity, 'price': price, 'regime': regime})

    return equity_curve, trades, total_commission


# ============================================================
# 主程序
# ============================================================
if __name__ == "__main__":
    print("="*60)
    print("BTC-USD LEAPS 策略 10 年回测 (2014-2024)")
    print("="*60)

    # 生成数据
    print("\n[1] 生成 BTC 模拟数据...")
    df = generate_btc_data()
    print(f"  数据范围: {df['Date'].iloc[0].date()} 到 {df['Date'].iloc[-1].date()}")
    print(f"  总天数: {len(df)}")
    print(f"  起点价: ${df['Close'].iloc[0]:,.0f}")
    print(f"  终点价: ${df['Close'].iloc[-1]:,.0f}")
    print(f"  10年涨幅: {(df['Close'].iloc[-1]/df['Close'].iloc[0]-1)*100:,.0f}%")

    # 加 MA25
    print("\n[2] 计算 25 日均线 + 牛熊...")
    df = add_market_regime(df, 25)
    print(f"  牛市天数: {(df['Regime']=='bull').sum()} ({(df['Regime']=='bull').mean()*100:.1f}%)")
    print(f"  熊市天数: {(df['Regime']=='bear').sum()} ({(df['Regime']=='bear').mean()*100:.1f}%)")

    # 回测
    print("\n[3] 运行回测 (含滑点+手续费)...")
    equity_curve, trades, total_commission = backtest(df, initial_cash=100000)
    print(f"  总手续费: ${total_commission:,.0f}")

    # 统计
    df_eq = pd.DataFrame(equity_curve)
    initial = 100000
    final = df_eq['equity'].iloc[-1]
    total_return = (final / initial - 1) * 100
    years = (df['Date'].iloc[-1] - df['Date'].iloc[0]).days / 365
    annual_return = ((final/initial) ** (1/years) - 1) * 100

    df_eq['peak'] = df_eq['equity'].cummax()
    df_eq['dd'] = (df_eq['equity'] - df_eq['peak']) / df_eq['peak']
    max_dd = df_eq['dd'].min() * 100

    df_trades = pd.DataFrame(trades)
    n_buy = len(df_trades[df_trades['action']=='buy'])
    n_tp = len(df_trades[df_trades['action']=='take_profit'])
    n_sl = len(df_trades[df_trades['action']=='stop_loss'])
    n_exp = len(df_trades[df_trades['action']=='expire'])

    closed = df_trades[df_trades['action'].isin(['take_profit', 'stop_loss', 'expire'])]
    wins = closed[closed['pnl'] > 0]
    win_rate = len(wins) / len(closed) * 100 if len(closed) > 0 else 0

    print(f"\n{'='*60}")
    print("📊 BTC 回测结果")
    print(f"{'='*60}")
    print(f"  初始资金:    ${initial:>15,.0f}")
    print(f"  最终资产:    ${final:>15,.0f}")
    print(f"  总回报:      {total_return:>14.0f}%")
    print(f"  年化回报:    {annual_return:>14.1f}%")
    print(f"  最大回撤:    {max_dd:>14.1f}%")
    print()
    print(f"  交易次数:    {n_buy:>15d}")
    print(f"    止盈:      {n_tp:>15d}")
    print(f"    止损:      {n_sl:>15d}")
    print(f"    到期:      {n_exp:>15d}")
    print(f"  胜率:        {win_rate:>14.1f}%")
    print(f"  总手续费:    ${total_commission:>14,.0f}")
    print(f"{'='*60}")

    # 对比 BTC 持有
    btc_final = df['Close'].iloc[-1] / df['Close'].iloc[0] * initial
    btc_annual = ((btc_final/initial) ** (1/years) - 1) * 100
    print(f"\n  📈 对比: 持有 BTC 10 年")
    print(f"  BTC 终值:    ${btc_final:>15,.0f}")
    print(f"  BTC 年化:    {btc_annual:>14.1f}%")
    print(f"  策略超额:    {annual_return - btc_annual:>14.1f}%")

    # 跟 SOXX 对比
    print(f"\n  📊 对比: BTC 策略 vs SOXX 策略")
    print(f"  BTC 年化:   {annual_return:>14.1f}%")
    print(f"  BTC 最大回撤: {max_dd:>13.1f}%")
    print(f"  胜率:        {win_rate:>14.1f}%")
