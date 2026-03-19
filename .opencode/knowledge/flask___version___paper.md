# Flask / Version / Paper

- [UPDATE] `flask_version_paper_trading_api: 2.3.4` | supersedes:flask_version_webhook_later
- [FACT] `sprint_paper_trading_risk_start: April 13, 2024`
- [FACT] `sprint_paper_trading_risk_end: April 26, 2024`
- [FACT] `celery_version_paper_trading: 5.3.0`
- [FACT] `risk_per_trade_max: 2% capital risk per trade`
- [DECISION] Because the user repeatedly tied paper trading to logging, alerting, and compliance, the next useful implementation step is likely an integrated `paper_trade.py` design with `RiskManager`, `TradeLogger`, Redis cache, and mock-based `pytest 7.3.1` coverage | supersedes:none
- [FACT] `alpaca_api_version_user_recent: v2.0`
