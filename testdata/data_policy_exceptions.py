DATA_POLICY_EXCEPTIONS = {
    "BK": {
        "category": "SAFE_KNOWN_EXCEPTION",
        "reason": "ticker_change_to_BNY",
        "active_trading_allowed": False,
        "blocks_pipeline": False,
        "blocks_pre_submit": False,
    },
    "PARA": {
        "category": "SAFE_KNOWN_EXCEPTION",
        "reason": "delisting_skydance_merger",
        "active_trading_allowed": False,
        "blocks_pipeline": False,
        "blocks_pre_submit": False,
    }
}
