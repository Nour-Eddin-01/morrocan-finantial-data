# 11_BVC_SSL_AND_LIVE_RUN_VALIDATION.md

# TradeHub Data - BVC SSL and Live Run Validation Specification

## 1. Purpose

This document defines the final manual validation step before adding scheduler or worker execution for the BVC price pipeline.

The goal is to run the real BVC live JSON collection path with SSL verification enabled, store all collected raw JSON pages, run diagnostics, normalize the pages, and verify that the output matches the already validated manual multi-page fixture behavior.

This phase must preserve the core project rule:

```txt
collect raw data first, normalize later
```

The live command being validated is:

```bash
docker compose run --rm api python -m tradehub_data.pipelines.bvc_prices.runner --collect-live
```

This document does not authorize scheduler, API, or TradeHub integration work.

## 2. Why This Is Required Before Scheduler

The live multi-page BVC JSON collector has been implemented and tested with mocked HTTP responses.

Current validated implementation status:

```txt
JSON endpoint config implemented
offset pagination implemented
JSON parser implemented
JSON diagnostics implemented
normalizer dispatch supports JSON
runner --collect-live implemented
tests pass: 73
```

Mocked tests prove local behavior, but scheduler work must not begin until a real live run proves:

- Docker can verify the BVC TLS certificate chain, or a trusted CA/intermediate bundle can be provided safely.
- Page 1 and later pages can be collected from the discovered JSON endpoint.
- The real JSON response shape matches the parser assumptions.
- Diagnostics pass before normalization.
- The runner stores raw payloads before writing normalized records.
- The full live listing is complete enough for scheduled collection.
- Re-running the command remains idempotent.

Without this validation, scheduled runs could repeatedly fail on SSL, collect only page 1, normalize malformed payloads, or create incomplete market snapshots.

## 3. SSL Problem Summary

A direct request to the BVC JSON endpoint has failed in at least one local environment with:

```txt
curl: (60) SSL certificate problem: unable to get local issuer certificate
```

This indicates that the client environment could not build a trusted certificate chain for the source.

This does not justify disabling SSL verification.

Required policy:

```txt
BVC_PRICE_COLLECTOR_VERIFY_SSL=true
```

If the system CA store cannot verify the source, the operator must provide a trusted CA or intermediate bundle through:

```txt
BVC_PRICE_COLLECTOR_CA_BUNDLE_PATH=/path/in/container/bvc-ca-bundle.pem
```

The project must not commit private certificates, cookies, browser session headers, CSRF tokens, or any other secret material.

## 4. Current Live Validation Result

The latest clean Codex reproduction is the current source of truth.

Important correction:

```txt
An earlier manual run appeared to show direct API page 1 working.
A later focused investigation confirmed that Docker/httpx can retrieve page 1 and page 2 when SSL verification uses the CA bundle and the non-secret Accept-Language header is present.
Treat scheduler approval as blocked until the updated collector is validated with a controlled --collect-live run.
```

### 4.1 Local CA Bundle State

The local CA bundle setup was checked without committing certificate material:

```txt
certs/bvc-ca-bundle.pem exists
certs/ is ignored by git
certificate count in bundle = 121
```

The CA bundle must remain local and untracked.

### 4.2 Docker System CA Result

Docker system CA validation still fails against the direct API endpoint:

```txt
CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate
```

Current conclusion:

```txt
The default container CA store is not sufficient in this environment.
BVC_PRICE_COLLECTOR_CA_BUNDLE_PATH is required for verified TLS.
```

### 4.3 CA Bundle Result

Using the CA bundle from Docker removes the TLS verification error.

Current conclusion:

```txt
CA bundle fixes certificate verification.
SSL verification must remain enabled.
```

However, fixing TLS alone did not make the original collector-style request reliable.

The follow-up investigation found that the request also needs:

```txt
Accept-Language: fr-FR,fr;q=0.9,en;q=0.8
```

This is a stable, non-secret header and must not be confused with cookies, CSRF tokens, session IDs, WAF tokens, or authorization headers.

### 4.4 Direct API Page 1

The earlier direct API page 1 request with CA bundle timed out from Docker/httpx when the collector-style request did not include `Accept-Language`:

```txt
https://api.casablanca-bourse.com/fr/api/bourse_data/last_market_watches/action?page%5Blimit%5D=50&page%5Boffset%5D=0
```

Observed Docker/httpx result:

```txt
httpx.ReadTimeout
```

Observed curl result with CA bundle:

```txt
timed out after 60 seconds with 0 bytes received
/tmp/bvc_page1_api.json was not created
```

Updated conclusion:

```txt
Page 1 is collectable from Docker/httpx with verified TLS and safe non-secret headers, including Accept-Language.
The collector still needs controlled validation after the header is added.
```

### 4.5 Direct API Page 2

The earlier direct API page 2 request with CA bundle timed out from Docker/httpx when the collector-style request did not include `Accept-Language`:

```txt
https://api.casablanca-bourse.com/fr/api/bourse_data/last_market_watches/action?page%5Blimit%5D=50&page%5Boffset%5D=50
```

Observed Docker/httpx result:

```txt
httpx.ReadTimeout
```

Observed curl result with CA bundle:

```txt
timed out after 60 seconds with 0 bytes received
/tmp/bvc_page2_api.json was not created
```

Updated conclusion:

```txt
Page 2 is collectable from Docker/httpx with verified TLS and safe non-secret headers, including Accept-Language.
The collector still needs controlled validation after the header is added.
```

### 4.6 Proxy API Page 2

The proxy page 2 request also timed out from curl with the CA bundle:

```txt
https://www.casablanca-bourse.com/api/proxy/fr/api/bourse_data/last_market_watches/action?page%5Blimit%5D=50&page%5Boffset%5D=50
```

Observed result:

```txt
timed out after 60 seconds with 0 bytes received
/tmp/bvc_page2_proxy.json was not created
```

Updated conclusion:

```txt
Proxy page 2 is collectable with the local CA bundle and safe non-secret headers, including Accept-Language.
```

### 4.7 Browser Page 2

Browser DevTools still shows page 2 working and returning valid JSON containing instruments such as:

```txt
MED PAPER
MICRODATA
MINIERE TOUISSIT
MUTANDIS SCA
```

Current conclusion:

```txt
The endpoint and page 2 data exist, but Docker/terminal live HTTP collection is not reliable yet.
```

### 4.8 Scheduler Status

Scheduler approval remains blocked.

Reasons:

- TLS verification requires the CA bundle in this environment.
- Live HTTP requests require the safe non-secret Accept-Language header.
- The updated collector must complete a controlled --collect-live validation.
- The project must not add browser cookies, CSRF tokens, private headers, or session material as committed defaults.
- The project must not disable SSL verification to force live collection.

## 5. Page 2 Troubleshooting

Page 2 is the current live validation blocker. Troubleshooting must be deliberate and conservative.

### 5.1 Compare Browser and Terminal Requests

Compare the successful browser DevTools request with the terminal/Docker request.

Fields to compare:

```txt
request URL
query parameters
method
accept header
referer header
user-agent header
accept-language header
origin header if present
cache headers
response status
response headers
timing behavior
```

Do not copy cookies, authorization headers, CSRF tokens, browser session IDs, or private headers into code or documentation.

### 5.2 Test Safe Header Differences

It is acceptable to test non-secret, generic headers manually, such as:

```txt
Accept
Referer
User-Agent
Accept-Language
Cache-Control
```

Any header added to runtime defaults must be:

- non-secret
- stable
- justifiable from public browser behavior
- documented in the collector spec
- covered by mocked tests

### 5.3 Do Not Require Cookies by Default

The collector must not require browser cookies by default.

If investigation shows that page 2 only works with session cookies or WAF-issued state, scheduler work remains blocked until a safe, legal, and maintainable acquisition strategy is documented.

Do not commit:

```txt
Cookie
Authorization
X-CSRF-Token
session IDs
browser storage values
WAF challenge tokens
```

### 5.4 Keep SSL Verification Enabled

All page 2 troubleshooting must keep:

```env
BVC_PRICE_COLLECTOR_VERIFY_SSL=true
```

If a CA bundle is needed, use:

```env
BVC_PRICE_COLLECTOR_CA_BUNDLE_PATH=/app/certs/bvc-ca-bundle.pem
```

Do not use SSL bypass as a diagnostic shortcut for scheduler approval.

### 5.5 Avoid Aggressive Scraping

Troubleshooting must not:

- brute-force offsets
- rapidly retry page 2
- crawl unknown endpoints
- bypass source protections
- increase `BVC_PRICE_COLLECTOR_MAX_PAGES` without evidence
- add browser automation as a default collector path

### 5.6 TODO

Investigate whether page 2 requires one of the following:

```txt
browser session cookies
proxy-side state established by loading page 1
WAF behavior
different non-secret request headers
direct API host instead of proxy host
specific HTTP protocol behavior
source rate limiting or request timing
```

Document the confirmed cause before changing runtime collector behavior.

## 6. Safe CA Bundle Options

### 6.1 Use the System CA Store First

The Docker image installs `ca-certificates`. The first validation attempt should use the default system trust store with SSL verification enabled.

Required environment:

```env
BVC_PRICE_COLLECTOR_VERIFY_SSL=true
BVC_PRICE_COLLECTOR_CA_BUNDLE_PATH=
```

If this works, no custom CA bundle is needed.

### 6.2 Provide a Trusted CA or Intermediate Bundle

If system CA verification fails, provide a PEM bundle that contains the missing trusted intermediate or CA certificate required to validate the BVC certificate chain.

Expected container path example:

```env
BVC_PRICE_COLLECTOR_VERIFY_SSL=true
BVC_PRICE_COLLECTOR_CA_BUNDLE_PATH=/app/certs/bvc-ca-bundle.pem
```

The bundle must be operator-provided and local to the deployment environment.

Do not commit:

```txt
private keys
private CA files
internal-only certificates
cookies
session IDs
authorization headers
browser profile data
```

### 6.3 Do Not Disable SSL Verification

The following must not be used for production validation:

```env
BVC_PRICE_COLLECTOR_VERIFY_SSL=false
```

Disabling SSL verification would make the validation unsuitable for scheduler approval.

## 7. How to Mount or Provide the CA Bundle in Docker

Use a local, untracked path for the CA bundle.

Recommended local path:

```txt
./certs/bvc-ca-bundle.pem
```

Recommended container path:

```txt
/app/certs/bvc-ca-bundle.pem
```

Example one-off Docker Compose command:

```bash
docker compose run --rm \
  -e BVC_PRICE_COLLECTOR_VERIFY_SSL=true \
  -e BVC_PRICE_COLLECTOR_CA_BUNDLE_PATH=/app/certs/bvc-ca-bundle.pem \
  -v "$PWD/certs/bvc-ca-bundle.pem:/app/certs/bvc-ca-bundle.pem:ro" \
  api python -m tradehub_data.pipelines.bvc_prices.runner --collect-live
```

If a local `.env` file is used, it must remain uncommitted:

```env
BVC_PRICE_COLLECTOR_VERIFY_SSL=true
BVC_PRICE_COLLECTOR_CA_BUNDLE_PATH=/app/certs/bvc-ca-bundle.pem
```

If a Compose override is used, it must be local-only unless it contains no private paths or private material.

## 8. Endpoint Validation Before Running the Pipeline

The originally discovered browser proxy endpoint is:

```txt
https://www.casablanca-bourse.com/api/proxy/fr/api/bourse_data/last_market_watches/action
```

The direct API endpoint has also been validated for page 1 with the CA bundle:

```txt
https://api.casablanca-bourse.com/fr/api/bourse_data/last_market_watches/action
```

Required query parameters:

```txt
page[limit]=50
page[offset]=0
```

Page 2 validation must explicitly test:

```txt
page[offset]=50
```

Current live observation: page 2 hangs or times out from terminal/Docker for both the direct API host and the proxy host, even though browser DevTools can retrieve page 2.

Required safe request headers:

```txt
Accept: application/vnd.api+json
Referer: https://www.casablanca-bourse.com/fr/live-market/marche-actions-listing
User-Agent: configured BVC collector user agent
```

### 8.1 Validate With System CA

Example:

```bash
curl --fail --show-error --silent \
  -H "accept: application/vnd.api+json" \
  -H "referer: https://www.casablanca-bourse.com/fr/live-market/marche-actions-listing" \
  "https://www.casablanca-bourse.com/api/proxy/fr/api/bourse_data/last_market_watches/action?page%5Blimit%5D=50&page%5Boffset%5D=0"
```

Expected result:

```txt
HTTP success
content-type application/json
JSON body contains market_watch rows
```

### 8.2 Validate With a CA Bundle

Example:

```bash
curl --fail --show-error --silent \
  --cacert ./certs/bvc-ca-bundle.pem \
  -H "accept: application/vnd.api+json" \
  -H "referer: https://www.casablanca-bourse.com/fr/live-market/marche-actions-listing" \
  "https://www.casablanca-bourse.com/api/proxy/fr/api/bourse_data/last_market_watches/action?page%5Blimit%5D=50&page%5Boffset%5D=0"
```

### 8.3 Validate From Python in the API Container

Example with system CA:

```bash
docker compose run --rm api python -c "import httpx; r = httpx.get('https://www.casablanca-bourse.com/api/proxy/fr/api/bourse_data/last_market_watches/action?page%5Blimit%5D=50&page%5Boffset%5D=0', headers={'accept':'application/vnd.api+json','referer':'https://www.casablanca-bourse.com/fr/live-market/marche-actions-listing'}, timeout=20); print(r.status_code, r.headers.get('content-type')); print(r.text[:200])"
```

Example with a CA bundle:

```bash
docker compose run --rm \
  -v "$PWD/certs/bvc-ca-bundle.pem:/app/certs/bvc-ca-bundle.pem:ro" \
  api python -c "import httpx; r = httpx.get('https://www.casablanca-bourse.com/api/proxy/fr/api/bourse_data/last_market_watches/action?page%5Blimit%5D=50&page%5Boffset%5D=0', headers={'accept':'application/vnd.api+json','referer':'https://www.casablanca-bourse.com/fr/live-market/marche-actions-listing'}, timeout=20, verify='/app/certs/bvc-ca-bundle.pem'); print(r.status_code, r.headers.get('content-type')); print(r.text[:200])"
```

These commands are validation aids only. They must not be added to tests because tests must not depend on the live BVC network.

## 9. Live Pipeline Command

Run the full live pipeline manually only after endpoint SSL validation is understood.

### 9.1 With System CA

```bash
docker compose run --rm \
  -e BVC_PRICE_COLLECTOR_VERIFY_SSL=true \
  api python -m tradehub_data.pipelines.bvc_prices.runner --collect-live
```

### 9.2 With Operator-Provided CA Bundle

```bash
docker compose run --rm \
  -e BVC_PRICE_COLLECTOR_VERIFY_SSL=true \
  -e BVC_PRICE_COLLECTOR_CA_BUNDLE_PATH=/app/certs/bvc-ca-bundle.pem \
  -v "$PWD/certs/bvc-ca-bundle.pem:/app/certs/bvc-ca-bundle.pem:ro" \
  api python -m tradehub_data.pipelines.bvc_prices.runner --collect-live
```

### 9.3 Optional Pagination Safety Settings

Use the default first:

```env
BVC_PRICE_COLLECTOR_PAGE_LIMIT=50
BVC_PRICE_COLLECTOR_MAX_PAGES=5
```

Only change these values if the source behavior is observed and documented.

Do not increase `BVC_PRICE_COLLECTOR_MAX_PAGES` aggressively.

## 10. Expected Successful Output

The command prints JSON.

Expected successful shape:

```json
{
  "status": "success",
  "mode": "collect_live",
  "pages_found": 2,
  "pages_processed": 2,
  "pagination_complete": true,
  "total_rows_detected": 80,
  "total_rows_normalized": 80,
  "duplicate_symbols_count": 0,
  "errors_count": 0
}
```

Acceptable live variation:

```txt
pages_found >= 2
total_rows_detected around 80
total_rows_normalized around 80
duplicate_symbols_count = 0
status = success
```

The exact row count may change if the exchange listing changes. Any large deviation from the manually validated 80-row baseline must be reviewed before scheduler work.

Each page summary should show:

```txt
raw_payload_id
source_id
payload_hash
diagnostics_status = success
rows_detected > 0
parseable_rows_count = rows_detected
normalization_status = success
final_raw_payload_status = normalized
```

## 11. Failure Behavior

### 11.1 SSL Failure

Expected behavior:

- command returns `status = failed`
- no normalized data is written if no raw payloads were collected
- ingestion run metadata includes `error_type = ssl_certificate_error`
- error message mentions the CA bundle configuration when appropriate

Operator action:

```txt
provide a trusted CA/intermediate bundle through BVC_PRICE_COLLECTOR_CA_BUNDLE_PATH
```

Do not set SSL verification to false.

### 11.2 Page 1 Works but Page 2 Fails

Expected behavior:

- collector stores page 1 raw payload
- runner may return `partial_success` or `failed` depending on diagnostics/normalization state
- ingestion run records the failed page URL and error type
- output must make the incomplete pagination state visible

Scheduler approval must remain blocked until later pages are reliable.

### 11.3 Malformed JSON

Expected behavior:

- raw payload is stored only if collection succeeded and payload storage rules allow it
- diagnostics fail for malformed or unknown JSON shape
- normalizer does not run for failed diagnostics
- error includes enough source context to inspect the payload

### 11.4 Empty Response

Expected behavior:

- empty response body is treated as a fetch failure
- empty JSON page after one or more non-empty pages is a pagination stop condition
- empty first page is a validation failure unless the market is confirmed closed and the source intentionally returns no rows

### 11.5 Mismatched Trading Dates

Expected behavior:

- group output must expose source trading date per page when available
- mismatched dates across pages must be treated as a data quality failure or at least `partial_success`
- scheduler approval must remain blocked until the mismatch is understood

### 11.6 Duplicate Symbols Across Pages

Expected behavior:

- duplicate symbols are reported in group output
- normalization remains idempotent and must not create duplicate instruments, latest prices, or price bars
- scheduler approval remains blocked until duplicate symbols are explained

## 12. Acceptance Criteria Before Scheduler

Scheduler work may begin only after all of these criteria pass:

```txt
1. SSL verification remains enabled.
2. The API container can validate the BVC JSON endpoint using system CA or an operator-provided CA bundle.
3. The live runner command completes without disabling SSL verification.
4. At least two JSON pages are collected when the market listing is paginated.
5. All collected raw JSON pages are stored in raw_payloads.
6. Each raw payload has correct page metadata and pagination_group_id.
7. Diagnostics pass for every collected page.
8. total_rows_detected is close to the validated expected listing size.
9. total_rows_normalized matches total parseable rows.
10. duplicate_symbols_count is 0.
11. final raw payload statuses are normalized.
12. A second live run or equivalent same-payload re-run proves idempotency.
13. No tests depend on the live BVC network.
14. No private certificates, cookies, tokens, or session material are committed.
```

Recommended idempotency check:

```bash
docker compose run --rm api python -m tradehub_data.pipelines.bvc_prices.runner --collect-live
docker compose run --rm api python -m tradehub_data.pipelines.bvc_prices.runner --collect-live
```

The second run should not create duplicate instruments, latest prices, or price bars for the same logical market snapshot.

## 13. Out-of-Scope Items

This phase must not implement:

- scheduler or worker execution
- API endpoints
- TradeHub integration
- frontend UI
- disabled SSL verification
- committed private CA files
- committed cookies, CSRF tokens, authorization headers, or session IDs
- aggressive crawling
- fetching unknown endpoints
- production alerting or monitoring

## 14. Codex Implementation Checklist

When this specification is implemented or validated later, Codex must:

```txt
1. Re-read AGENTS.md and the BVC specs.
2. Inspect current SSL/client/config behavior.
3. Confirm Docker image includes ca-certificates.
4. Run endpoint validation with SSL verification enabled.
5. If needed, use only operator-provided CA bundle configuration.
6. Run the live pipeline command.
7. Inspect JSON output for pages, rows, diagnostics, normalization, duplicates, and errors.
8. Run the command twice or re-run by raw payload IDs to prove idempotency.
9. Do not add tests that hit the live network.
10. Do not implement scheduler/workers until acceptance criteria pass.
```
