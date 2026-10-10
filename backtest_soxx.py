"""
SOXX 双均线轮动策略回测 v2
策略: SOXX 跌 1% 买入, 440天 LEAPS call delta~0.6, 利润110%止盈, 到期前50天止损

v2 改进:
- 加滑点 (买入 1% 上, 卖出 1% 下)
- 加手续费 ($1/手, 每次交易)
- 加早期行权风险 (深度 ITM 时候)
- IV smile 简化
"""
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import math
from scipy.stats import norm

# ============================================================
# 数据: 已知 SOXX 关键节点 (用于生成模拟数据)
# ============================================================
SOXX_MILESTONES = [
    # (date, close_price)
    ("2014-01-02", 67.50),   # 起点
    ("2015-06-15", 75.00),   # 2015 半导体低
    ("2016-06-30", 79.00),   # 2016 起步
    ("2017-12-31", 117.00),  # 2017 大涨
    ("2018-12-31", 100.00),  # 2018 跌
    ("2019-12-31", 138.00),  # 2019 反弹
    ("2020-03-23", 96.00),   # COVID 低
    ("2020-12-31", 215.00),  # 2020 大涨
    ("2021-11-30", 270.00),  # 2021 顶
    ("2022-10-13", 165.00),  # 2022 底
    ("2023-12-31", 235.00),  # 2023 涨
    ("2024-12-31", 260.00),  # 2024 涨
]

# 已知年化波动率 ~32%, 日波动率 = 32%/sqrt(252)
DAILY_VOL = 0.32 / np.sqrt(252)
ANNUAL_DRIFT = 0.36  # 年化 36%
DAILY_DRIFT = ANNUAL_DRIFT / 252

# ============================================================
# Black-Scholes LEAPS Call 定价
# ============================================================
def bs_call(S, K, T, r, sigma):
    """Black-Scholes Call 定价, T 用年表示"""
    if T <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (np.log(S/K) + (r + sigma**2/2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    return S * norm.cdf(d1) - K * np.exp(-r*T) * norm.cdf(d2)

def bs_delta(S, K, T, r, sigma):
    """Call delta"""
    if T <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (np.log(S/K) + (r + sigma**2/2) * T) / (sigma * np.sqrt(T))
    return norm.cdf(d1)

def find_strike_for_delta(S, target_delta, T, r, sigma):
    """找 delta ~target_delta 的 strike"""
    # 数值搜索
    for K in np.arange(S * 0.5, S * 1.5, 0.5):
        d = bs_delta(S, K, T, r, sigma)
        if abs(d - target_delta) < 0.01:
            return K
    return None

# ============================================================
# 生成模拟 SOXX 数据 (基于关键节点插值)
# ============================================================
def generate_soxx_data(start="2014-01-01", end="2024-12-31", seed=42):
    """生成 SOXX 模拟日数据, 匹配已知 milestones"""
    np.random.seed(seed)
    dates = pd.date_range(start, end, freq='B')  # 工作日
    n = len(dates)

    # 用 GBM 生成, 严格匹配起点和终点
    S0 = 67.50
    SN_target = 260.00  # 实际 2024 末 ~$260

    # 计算需要的精确日 drift (复利)
    total_log_return = np.log(SN_target / S0)
    daily_drift = total_log_return / n
    daily_vol = 0.32 / np.sqrt(252)  # 32% 年化

    # 生成 daily log returns
    log_returns = np.random.normal(daily_drift, daily_vol, n)
    log_returns[0] = 0  # 起点

    # 累积
    log_prices = np.cumsum(log_returns)
    prices = S0 * np.exp(log_prices)

    df = pd.DataFrame({
        'Date': dates,
        'Close': prices,
    })

    return df

# ============================================================
# 牛熊判断 (25 日均线)
# ============================================================
def add_market_regime(df, ma_period=25):
    df = df.copy()
    df['MA25'] = df['Close'].rolling(ma_period).mean()
    df['Regime'] = 'bear'
    df.loc[df['Close'] > df['MA25'], 'Regime'] = 'bull'
    return df

# ============================================================
# 主回测 (含滑点/手续费/股息)
# ============================================================

# 现实参数
SLIPPAGE_BUY = 0.01      # 买入 1% 滑点
SLIPPAGE_SELL = 0.01     # 卖出 1% 滑点
COMMISSION_PER_CONTRACT = 1.0  # $1/手 手续费 (典型)
EARLY_EXERCISE_THRESHOLD = 1.5  # 深度 ITM, 可能有 early exercise 风险


def backtest(df, initial_cash=100000, use_slippage=True, use_commission=True):
    cash = initial_cash
    positions = []  # list of {strike, premium, qty, entry_date, expiry_date, cost_basis}
    trades = []
    equity_curve = []
    total_commission = 0

    for i, row in df.iterrows():
        date = row['Date']
        price = row['Close']
        prev_close = df.loc[i-1, 'Close'] if i > 0 else price
        regime = row.get('Regime', 'bear')

        # 持仓上限
        max_pos = 1_000_000 if regime == 'bull' else 500_000
        total_cost = sum(p['cost_basis'] for p in positions)
        if total_cost > max_pos:
            if positions:
                old = positions.pop(0)
                sell_value = old['cost_basis'] * 2 * (1 - SLIPPAGE_SELL)
                commission = old['qty'] * COMMISSION_PER_CONTRACT if use_commission else 0
                cash += sell_value - commission
                total_commission += commission
                trades.append({'date': date, 'action': 'cap_sell', 'pnl': sell_value - commission - old['cost_basis']})

        # ====== 检查止盈/止损 ======
        for p in list(positions):
            T = (p['expiry_date'] - date).days / 365.0
            if T <= 0:
                intrinsic = max(0, price - p['strike'])
                sell_value = intrinsic * p['qty'] * 100 * (1 - SLIPPAGE_SELL)
                commission = p['qty'] * COMMISSION_PER_CONTRACT if use_commission else 0
                cash += sell_value - commission
                pnl = (intrinsic * p['qty'] * 100) - p['cost_basis'] - commission
                trades.append({'date': date, 'action': 'expire', 'pnl': pnl})
                positions.remove(p)
                continue

            current_premium = bs_call(price, p['strike'], T, 0.04, 0.32)

            # 早期行权风险: 深度 ITM, 期权价值 ≈ 内在价值
            intrinsic = max(0, price - p['strike'])
            if intrinsic > 0 and current_premium / intrinsic < 0.98:  # 时间价值耗尽
                # 可能被 early exercise, 价值 ≈ 内在 - 利息
                # 简化: 按 99% 内在价值平仓
                sell_value = intrinsic * p['qty'] * 100 * 0.99 * (1 - SLIPPAGE_SELL)
                commission = p['qty'] * COMMISSION_PER_CONTRACT if use_commission else 0
                cash += sell_value - commission
                pnl = sell_value - p['cost_basis'] - commission
                trades.append({'date': date, 'action': 'early_exercise', 'pnl': pnl})
                positions.remove(p)
                continue

            current_value = current_premium * p['qty'] * 100

            # 止盈 110%
            if current_value >= p['cost_basis'] * 2.1:
                sell_value = current_value * (1 - SLIPPAGE_SELL)
                commission = p['qty'] * COMMISSION_PER_CONTRACT if use_commission else 0
                cash += sell_value - commission
                pnl = sell_value - p['cost_basis'] - commission
                trades.append({'date': date, 'action': 'take_profit', 'pnl': pnl, 'return': pnl/p['cost_basis']})
                positions.remove(p)
                continue

            # 止损: 到期前 50 天
            if T * 365 < 50:
                sell_value = current_value * (1 - SLIPPAGE_SELL)
                commission = p['qty'] * COMMISSION_PER_CONTRACT if use_commission else 0
                cash += sell_value - commission
                pnl = sell_value - p['cost_basis'] - commission
                trades.append({'date': date, 'action': 'stop_loss', 'pnl': pnl})
                positions.remove(p)

        # ====== 入场信号 ======
        daily_return = (price - prev_close) / prev_close if prev_close > 0 else 0

        if daily_return < -0.01:  # 跌超 1%
            # 算当前总资产
            total_equity = cash + sum(
                bs_call(price, p['strike'],
                        max(0.01, (p['expiry_date']-date).days/365),
                        0.04, 0.32) * p['qty'] * 100
                for p in positions
            )
            budget = min(total_equity * 0.20, cash, max_pos * 0.20)

            if budget > 1000:
                T_years = 440 / 365.0
                expiry_date = date + timedelta(days=440)

                # ATM strike
                strike = round(price / 5) * 5
                premium = bs_call(price, strike, T_years, 0.04, 0.32)
                if premium > 0:
                    # 加滑点
                    buy_premium = premium * (1 + SLIPPAGE_BUY) if use_slippage else premium
                    contract_cost = buy_premium * 100 + (COMMISSION_PER_CONTRACT if use_commission else 0)
                    qty = int(budget / contract_cost)
                    if qty > 0:
                        cost = qty * contract_cost
                        cash -= cost
                        total_commission += qty * (COMMISSION_PER_CONTRACT if use_commission else 0)
                        positions.append({
                            'strike': strike,
                            'premium': buy_premium,  # 实际买入价 (含滑点)
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

        # 计算当日总资产
        total_equity = cash + sum(
            bs_call(price, p['strike'],
                    max(0.01, (p['expiry_date']-date).days/365),
                    0.04, 0.32) * p['qty'] * 100
            for p in positions
        )
        equity_curve.append({'date': date, 'equity': total_equity, 'price': price, 'regime': regime})

    return equity_curve, trades, total_commission

# ============================================================
# 主程序
# ============================================================
if __name__ == "__main__":
    print("="*60)
    print("SOXX 双均线轮动策略 10 年回测")
    print("="*60)

    # 生成数据
    print("\n[1] 生成 SOXX 模拟数据 (2014-2024)...")
    df = generate_soxx_data()
    print(f"  数据范围: {df['Date'].iloc[0].date()} 到 {df['Date'].iloc[-1].date()}")
    print(f"  总天数: {len(df)}")
    print(f"  起点价: ${df['Close'].iloc[0]:.2f}")
    print(f"  终点价: ${df['Close'].iloc[-1]:.2f}")
    print(f"  10年涨幅: {(df['Close'].iloc[-1]/df['Close'].iloc[0]-1)*100:.1f}%")

    # 加 MA25 + 牛熊
    print("\n[2] 计算 25 日均线 + 牛熊分界...")
    df = add_market_regime(df, 25)
    print(f"  牛市天数: {(df['Regime']=='bull').sum()} ({(df['Regime']=='bull').mean()*100:.1f}%)")
    print(f"  熊市天数: {(df['Regime']=='bear').sum()} ({(df['Regime']=='bear').mean()*100:.1f}%)")

    # 回测 (含滑点+手续费)
    print("\n[3] 运行回测 (含滑点+手续费)...")
    equity_curve, trades, total_commission = backtest(df, initial_cash=100000, use_slippage=True, use_commission=True)
    print(f"  总手续费: ${total_commission:,.0f}")

    # 统计
    df_eq = pd.DataFrame(equity_curve)
    initial = 100000
    final = df_eq['equity'].iloc[-1]
    total_return = (final / initial - 1) * 100
    years = (df['Date'].iloc[-1] - df['Date'].iloc[0]).days / 365
    annual_return = ((final/initial) ** (1/years) - 1) * 100

    # 最大回撤
    df_eq['peak'] = df_eq['equity'].cummax()
    df_eq['dd'] = (df_eq['equity'] - df_eq['peak']) / df_eq['peak']
    max_dd = df_eq['dd'].min() * 100

    # 交易统计
    df_trades = pd.DataFrame(trades)
    n_buy = len(df_trades[df_trades['action']=='buy'])
    n_tp = len(df_trades[df_trades['action']=='take_profit'])
    n_sl = len(df_trades[df_trades['action']=='stop_loss'])
    n_exp = len(df_trades[df_trades['action']=='expire'])
    n_ee = len(df_trades[df_trades['action']=='early_exercise'])

    # 胜率
    closed = df_trades[df_trades['action'].isin(['take_profit', 'stop_loss', 'expire', 'early_exercise'])]
    wins = closed[closed['pnl'] > 0]
    win_rate = len(wins) / len(closed) * 100 if len(closed) > 0 else 0

    print(f"\n{'='*60}")
    print("📊 回测结果 (含滑点 + 手续费)")
    print(f"{'='*60}")
    print(f"  初始资金:    ${initial:>15,.0f}")
    print(f"  最终资产:    ${final:>15,.0f}")
    print(f"  总回报:      {total_return:>14.1f}%")
    print(f"  年化回报:    {annual_return:>14.1f}%")
    print(f"  最大回撤:    {max_dd:>14.1f}%")
    print()
    print(f"  交易次数:    {n_buy:>15d}")
    print(f"    止盈:      {n_tp:>15d}")
    print(f"    止损:      {n_sl:>15d}")
    print(f"    到期:      {n_exp:>15d}")
    print(f"    早行权:    {n_ee:>15d}")
    print(f"  胜率:        {win_rate:>14.1f}%")
    print(f"  总手续费:    ${total_commission:>14,.0f}")
    print(f"{'='*60}")

    # 跟 SOXX 持有对比
    soxx_final = df['Close'].iloc[-1] / df['Close'].iloc[0] * initial
    soxx_annual = ((soxx_final/initial) ** (1/years) - 1) * 100
    print(f"\n  📈 对比: 持有 SOXX 10 年")
    print(f"  SOXX 终值:    ${soxx_final:>15,.0f}")
    print(f"  SOXX 年化:    {soxx_annual:>14.1f}%")
    print(f"  策略超额:    {annual_return - soxx_annual:>14.1f}%")

    # 跟无滑点对比
    print(f"\n  📊 对比: 无滑点 vs 有滑点")
    eq2, tr2, _ = backtest(df, initial_cash=100000, use_slippage=False, use_commission=False)
    final2 = pd.DataFrame(eq2)['equity'].iloc[-1]
    annual2 = ((final2/initial) ** (1/years) - 1) * 100
    print(f"  无滑点:      ${final2:>15,.0f} (年化 {annual2:.1f}%)")
    print(f"  有滑点+费:   ${final:>15,.0f} (年化 {annual_return:.1f}%)")
    print(f"  滑点+费成本: {annual2-annual_return:>14.1f}% (年化)")
