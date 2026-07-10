import numpy as np

def calculate_transaction_costs(notional: float, shares: float, params: dict) -> dict[str, float]:
    """
    Calculates transaction costs based on notional value, shares traded, and configuration parameters.
    
    Parameters:
    - notional: Gross notional value of the trade (shares * price).
    - shares: Number of shares traded.
    - params: Dictionary containing strategy parameters.
    
    Configuration parameters (in params):
    - commission_per_share: Fixed fee per share (e.g. 0.005 USD/share). Default: 0.0
    - slippage_bps: Slippage in basis points (e.g. 10 bps = 10/10000 = 0.001). Default: 0.0
    - min_commission: Minimum commission per trade (e.g. 1.0 USD). Default: 0.0
    
    Returns:
    A dictionary with keys:
    - "commission_cost": Float representing commission paid.
    - "slippage_cost": Float representing slippage cost.
    - "total_cost": commission_cost + slippage_cost.
    """
    if shares <= 0 or notional <= 0:
        return {
            "commission_cost": 0.0,
            "slippage_cost": 0.0,
            "total_cost": 0.0
        }

    commission = float(params.get("commission_per_share", 0.0)) * float(shares)
    min_comm = float(params.get("min_commission", 0.0))
    if commission > 0.0 and commission < min_comm:
        commission = min_comm
    elif commission == 0.0 and min_comm > 0.0:
        commission = min_comm
        
    slippage_bps = float(params.get("slippage_bps", 0.0))
    slippage = notional * (slippage_bps / 10000.0)
    
    return {
        "commission_cost": round(commission, 4),
        "slippage_cost": round(slippage, 4),
        "total_cost": round(commission + slippage, 4)
    }


def calculate_max_shares(target_notional: float, price: float, cash: float, params: dict) -> float:
    """
    Calculates the maximum shares that can be bought given a target gross notional,
    asset price, available cash, and transaction cost parameters, ensuring cash does not go negative.
    """
    notional = min(target_notional, cash)
    if notional <= 0 or price <= 0 or cash <= 0:
        return 0.0
        
    slippage_bps = float(params.get("slippage_bps", 0.0))
    comm_per_share = float(params.get("commission_per_share", 0.0))
    min_comm = float(params.get("min_commission", 0.0))
    
    # cost_factor handles slippage
    cost_factor = 1.0 + (slippage_bps / 10000.0)
    
    # 1) Solve assuming flat min commission applies:
    # shares * price * cost_factor + min_comm <= cash
    shares_a = max(0.0, (cash - min_comm) / (price * cost_factor))
    
    # 2) Solve assuming comm_per_share applies:
    # shares * (price * cost_factor + comm_per_share) <= cash
    shares_b = max(0.0, cash / (price * cost_factor + comm_per_share))
    
    # Choose option based on which commission regime dominates
    if shares_b * comm_per_share >= min_comm:
        shares = shares_b
    else:
        shares = shares_a
        
    # Cap shares by the gross target notional
    target_shares = target_notional / price
    
    # Verification check to see if target_shares fits in cash after costs
    target_comm = target_shares * comm_per_share
    if target_comm > 0.0 and target_comm < min_comm:
        target_comm = min_comm
    elif target_comm == 0.0 and min_comm > 0.0:
        target_comm = min_comm
        
    target_slippage = target_notional * (slippage_bps / 10000.0)
    
    if target_notional + target_comm + target_slippage <= cash:
        return target_shares
    else:
        return min(shares, target_shares)
