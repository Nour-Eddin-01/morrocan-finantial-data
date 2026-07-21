BVC_PRICE_COLLECTOR_NAME = "bvc_price_collector"
BVC_PRICE_SOURCE_CODE = "bvc_prices"
BVC_PRICE_SOURCE_NAME = "Bourse de Casablanca Prices"
BVC_PRICE_PAYLOAD_TYPE = "bvc_price_snapshot"
BVC_EQUITY_PRICE_DATASET_CODE = "bvc_equity_prices"
BVC_PRICE_HTML_SOURCE_ENDPOINT = "bvc_price_snapshot_html"
BVC_PRICE_JSON_SOURCE_ENDPOINT = "bvc_price_snapshot_json_page"
BVC_JSON_REQUEST_PROFILE = "bvc-json-safe-v1"
BVC_HTML_REQUEST_PROFILE = "bvc-html-safe-v1"
BVC_FIXTURE_REQUEST_PROFILE = "bvc-fixture-safe-v1"
BVC_MAX_REDIRECT_HOPS = 5
DEFAULT_BVC_BASE_URL = "https://www.casablanca-bourse.com"
DEFAULT_BVC_PRICE_SOURCE_PATHS = ("/fr/live-market/marche-actions-listing?amp=1",)
DEFAULT_BVC_PRICE_JSON_PATH = "/api/proxy/fr/api/bourse_data/last_market_watches/action"
DEFAULT_BVC_PRICE_JSON_ACCEPT = "application/vnd.api+json"
DEFAULT_BVC_PRICE_JSON_REFERER = "https://www.casablanca-bourse.com/fr/live-market/marche-actions-listing"
DEFAULT_BVC_ACCEPT_LANGUAGE = "fr-FR,fr;q=0.9,en;q=0.8"
DEFAULT_BVC_USER_AGENT = "TradeHubDataBot/0.1"
DEFAULT_ALLOWED_DOMAINS = ("casablanca-bourse.com", "www.casablanca-bourse.com")
TEMPORARY_STATUS_CODES = {429, 500, 502, 503, 504}
