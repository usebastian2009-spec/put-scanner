# Free Put Scanner configuration
MIN_PRICE = 10.0
MAX_PRICE = 70.0

# Universe: "nasdaq" = every US-listed stock from the Nasdaq screener in the
# price range and above MIN_MARKET_CAP, plus TICKERS. "list" = only TICKERS.
UNIVERSE = "nasdaq"
MIN_MARKET_CAP = 2_000_000_000

# ---- "Like AFRM" profile -------------------------------------------------
# Daily RSI(14) window. RSI_USE_LIVE_BAR=True uses today's bar in progress
# (what the daily chart shows right now); False uses the last completed close.
RSI_MIN = 30.0
RSI_MAX = 50.0
RSI_USE_LIVE_BAR = True
# High beta: 1-year beta vs SPY from daily returns (computed here, not Yahoo's).
# 2.0 = twice as volatile as the market (higher weekly premiums) without
# leaving only the most speculative names (3.0 left almost nothing profitable).
MIN_BETA = 2.0
BETA_LOOKBACK_DAYS = 252
# Trailing P/E must be positive (profitable) and at most MAX_PE.
MAX_PE = 60.0
# Premium: yield on collateral normalised to 7 days (premium/strike * 7/DTE).
MIN_WEEKLY_YIELD = 0.008   # 0.80% per week
# --------------------------------------------------------------------------

# ---- Tesis: watchlist de compañías sólidas con beta alta ------------------
# Vendo puts en compañías fundamentalmente sólidas, con suficiente cash para
# sobrevivir una crisis, y con beta alta para cobrar primas más gordas. Si me
# asignan, me quedo con una compañía que quiero tener; el watchlist rota solo.
# Datos de Yahoo (último año / último trimestre). Un dato faltante = no pasa.
FUNDAMENTALS_FILTER = True
# Supervivencia (TODAS obligatorias)
MIN_CURRENT_RATIO = 1.2       # activos corrientes / pasivos corrientes
# Deuda respaldada: pasa si el cash cubre >= MIN_CASH_TO_DEBT de la deuda, O si la
# compañía es dueña de sus activos (terreno, energía, equipo) y la deuda está cubierta
# por el patrimonio (deuda/patrimonio <= ASSET_BACKED_MAX_DE). Caso IREN.
MIN_CASH_TO_DEBT = 0.5
ASSET_BACKED_MAX_DE = 1.0
# Se financia sola: cash OPERATIVO positivo. El capex de expansión NO cuenta como
# quema (por eso no se usa el free cash flow). Si el cash operativo es negativo,
# la caja tiene que cubrir >= MIN_RUNWAY_YEARS de esa quema.
MIN_RUNWAY_YEARS = 2.0
# Calidad (hay que pasar al menos MIN_QUALITY_PASS de 3)
MIN_REVENUE_GROWTH = 0.0      # ventas creciendo vs el año anterior
MAX_DEBT_TO_EQUITY = 1.5      # deuda / patrimonio
# (el tercero: margen operativo positivo)
MIN_QUALITY_PASS = 2
# --------------------------------------------------------------------------

# Weekly puts: sell the first expiration 3-10 days out (the upcoming full week)
MIN_DTE = 3
MAX_DTE = 10
WEEKLY_EXPIRATIONS = 1
# Drop the stock if earnings land on or before the expiration we would sell
EXCLUDE_EARNINGS_BEFORE_EXPIRY = True

# Option-chain map: strikes around spot (half below, half above), all expirations <= GEX_MAX_DTE
STRIKE_MAP_COUNT = 20
# Biggest individual contracts to list (by open interest and by volume)
BIG_CONTRACTS_TOP = 10
UNUSUAL_MIN_VOLUME = 500

# Option filters
MIN_DELTA = 0.12       # absolute delta
MAX_DELTA = 0.30
MIN_OI = 100
MIN_OPTION_VOLUME = 10
MAX_BID_ASK_SPREAD_PCT = 0.25
MAX_BID_ASK_SPREAD_ABS = 0.10   # a tight spread in cents also passes (cheap weeklies)

# Premium filter (weekly premiums are smaller than monthly ones)
MIN_PREMIUM_YIELD = 0.002  # 0.20% of cash collateral

# Greeks (Yahoo does not provide them; computed with Black-Scholes from IV)
RISK_FREE_RATE = 0.04
MIN_IV = 0.05
MAX_IV = 5.0               # Yahoo sometimes returns garbage IVs on illiquid strikes

# Gamma calculation: aggregates every expiration up to GEX_MAX_DTE
CONTRACT_SIZE = 100
GEX_WINDOW = 0.20          # +/-20% around spot for levels and the zero-gamma search
GEX_MAX_DTE = 60
GEX_TOP_LEVELS = 3         # how many positive / negative strikes to list

# RSI (Wilder, same as TradingView) on split-adjusted, non-dividend-adjusted closes
RSI_PERIOD = 14
DIVERGENCE_LOOKBACK = 60
PIVOT_LEFT = 3
PIVOT_RIGHT = 3

# Yahoo daily bars sometimes skip whole sessions; rebuild them from hourly bars
HOURLY_FILL_PERIOD = "60d"
CALENDAR_REFERENCE = "SPY"

# Safety filters
AVOID_EARNINGS_WITHIN_DAYS = 7
MIN_AVG_DOLLAR_VOLUME = 10_000_000

# Number of finalists
TOP_N = 15

# Put candidates to display per stock
PUTS_PER_STOCK = 3

# Network politeness
REQUEST_PAUSE = 0.4
RETRIES = 3

# Start with a hand-picked liquid universe.
# Expand later once the scanner is working reliably.
TICKERS = [
    "AAPL","AMD","AMZN","BAC","COIN","CRWD","CRWV","CVNA","DKNG",
    "HOOD","IREN","META","MSTR","MU","NFLX","NVDA","ORCL","PLTR",
    "RBLX","SOFI","TSLA","TSLL","UBER","AFRM","RKLB","SHOP","SNOW",
    "TGT","XOM","CVX","JPM","WMT","GOOG","MSFT"
]
