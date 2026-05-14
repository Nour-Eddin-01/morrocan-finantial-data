BVC_PRICE_COLLECTOR_NAME = "bvc_price_collector"
BVC_PRICE_SOURCE_CODE = "bvc_prices"
BVC_PRICE_SOURCE_NAME = "Bourse de Casablanca Prices"
BVC_PRICE_PAYLOAD_TYPE = "bvc_price_snapshot"
DEFAULT_BVC_BASE_URL = "https://www.casablanca-bourse.com"
DEFAULT_BVC_PRICE_SOURCE_PATHS = ("/fr/live-market/marche-actions-listing?amp=1",)
DEFAULT_BVC_USER_AGENT = "TradeHubDataBot/0.1"
DEFAULT_ALLOWED_DOMAINS = ("casablanca-bourse.com", "www.casablanca-bourse.com")
TEMPORARY_STATUS_CODES = {429, 500, 502, 503, 504}
