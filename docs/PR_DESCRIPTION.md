# Add simple volatility-adjusted momentum agent

The original daily path requires several heavy dependencies and manual settlement, and its sample submit endpoint/schema version are not verified. Add a separate `run_simple.py` path that keeps the team's TSMC/financial/momentum allocation while using ATR-normalized signals, bounded crossover scores and inverse-square-root-volatility sizing.

Includes automatic market retrieval and official account-sync adapter, bounded Gemini review, whole-lot derive-orders.v2 generation, JSON Schema/business validation, all30 ETF Active Share checks, explicit acceptance receipts, a frozen offline demo, Windows launchers/schedule, and Chinese usage/rules documentation. Existing graph path is untouched.

Validation:29 unittest checks and frozen demo pass; demo creates26 holdings/26 orders. No performance or excess-return claim. No authenticated Gemini, paid ETF data, organizer API, Windows scheduler or live submission was tested. The default adapter remains disabled until real endpoint/receipt mapping and official schema4.0 vs4.1 are confirmed. TWSE confirms2026-10-26 is a holiday despite the contest document's stated start date.

Base:613778b08bcc1b2817bc2ab88746b08d9da58460. No secrets or local portfolio runtime included. Merge only into the team's authorized repository.
