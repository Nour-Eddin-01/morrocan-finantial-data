# 14_BVC_LIVE_HTTP_TIMEOUT_INVESTIGATION.md

# TradeHub Data - BVC Live HTTP Timeout Investigation

## 1. Purpose

This document defines a focused, safe investigation plan for the current BVC live JSON HTTP timeout blocker.

The goal is to understand why BVC JSON endpoints can be retrieved from browser DevTools but time out from Docker/httpx and terminal/curl, without weakening security or adding automation prematurely.

This is an investigation/specification document only. It does not authorize scheduler work, TradeHub integration, live collection approval, aggressive scraping, or SSL verification bypasses.

## 2. Current Known Facts

Current validated state:

- manual multi-page fixture collection works
- parser, diagnostics, normalizer, and pipeline runner work with saved fixtures
- JSON collector path exists and is tested with mocked HTTP responses
- Docker system CA fails with `CERTIFICATE_VERIFY_FAILED`
- a local CA bundle fixes TLS verification
- after TLS verification is fixed, Docker/httpx requests work when the non-secret `Accept-Language` header is present
- terminal/curl requests work when the local CA bundle and safe non-secret headers are used
- the current collector-style request without `Accept-Language` fails with a protocol/read error
- browser DevTools can retrieve page 2 JSON
- `--collect-live` requires a controlled validation with the updated collector before scheduler approval
- scheduler/workers remain blocked

Important policy:

```txt
SSL verification must stay enabled.
The project must not commit cookies, tokens, session IDs, private headers, or local certificate files.
```

## 2.1 Confirmed Cause And Safe Fix

The focused investigation found the smallest successful non-secret header addition for Docker/httpx proxy requests:

```txt
Accept-Language: fr-FR,fr;q=0.9,en;q=0.8
```

With SSL verification enabled and `BVC_PRICE_COLLECTOR_CA_BUNDLE_PATH` pointing to the local CA bundle:

```txt
proxy offset 0  -> HTTP 200, 50 rows
proxy offset 50 -> HTTP 200, 30 rows
```

The current collector-style request using only `Accept`, `Referer`, and the configured `User-Agent` fails. Adding `Accept-Language` is safe because it is a stable, non-secret language preference header and does not depend on browser session state.

The next runtime change should add:

```env
BVC_PRICE_COLLECTOR_ACCEPT_LANGUAGE=fr-FR,fr;q=0.9,en;q=0.8
```

and include it in BVC JSON live request headers.

## 3. Exact Failing Endpoints

### 3.1 Direct API Page 1

```txt
https://api.casablanca-bourse.com/fr/api/bourse_data/last_market_watches/action?page%5Blimit%5D=50&page%5Boffset%5D=0
```

Observed from Docker/httpx with CA bundle:

```txt
httpx.ReadTimeout
```

Observed from terminal/curl with CA bundle:

```txt
timed out after 60 seconds with 0 bytes received
```

### 3.2 Direct API Page 2

```txt
https://api.casablanca-bourse.com/fr/api/bourse_data/last_market_watches/action?page%5Blimit%5D=50&page%5Boffset%5D=50
```

Observed from Docker/httpx with CA bundle:

```txt
httpx.ReadTimeout
```

Observed from terminal/curl with CA bundle:

```txt
timed out after 60 seconds with 0 bytes received
```

### 3.3 Proxy API Page 2

```txt
https://www.casablanca-bourse.com/api/proxy/fr/api/bourse_data/last_market_watches/action?page%5Blimit%5D=50&page%5Boffset%5D=50
```

Observed from terminal/curl with CA bundle:

```txt
timed out after 60 seconds with 0 bytes received
```

Browser DevTools can retrieve page 2 JSON, so the data exists. The blocker is runtime HTTP behavior outside the browser.

## 4. Hypotheses

The timeout may be caused by one or more of these factors.

### 4.1 Missing Non-Secret Browser Headers

The browser may send ordinary, non-secret headers that affect server behavior.

Examples:

```txt
Accept
Accept-Language
Accept-Encoding
Referer
Origin
Cache-Control
Pragma
Sec-Fetch-Dest
Sec-Fetch-Mode
Sec-Fetch-Site
User-Agent
Connection
```

Only non-secret headers may be tested.

### 4.2 HTTP Protocol Behavior

The endpoint may behave differently with:

```txt
HTTP/1.1
HTTP/2
connection reuse
keep-alive
Connection: close
redirect handling
```

The current curl tests used `--http1.1`; httpx behavior should also be compared.

### 4.3 Compression Or Encoding Behavior

The browser may advertise compression that affects the server path.

Safe variants to test:

```txt
curl --compressed
Accept-Encoding: gzip, deflate, br
Accept-Encoding: gzip, deflate
Accept-Encoding: identity
```

### 4.4 IPv4 vs IPv6

Docker or the host resolver may choose a route that hangs.

Safe variants to test:

```txt
curl --ipv4
curl --ipv6
httpx with resolved address observation
```

Do not hardcode IP addresses into runtime collector defaults without a confirmed, stable, safe reason.

### 4.5 DNS Or Container Networking

Docker networking may differ from host networking.

Compare:

```txt
host curl
Docker container curl or Python/httpx
DNS resolution inside container
DNS resolution on host
```

### 4.6 Proxy Or WAF Behavior

The source may apply different behavior based on:

```txt
host
user agent
header set
request origin
TLS/client fingerprint
HTTP protocol
rate or timing
browser session state
```

This must be investigated without bypassing protections or committing private browser material.

### 4.7 Request Timing Or Rate Limiting

Timeouts may be related to request timing, repeated attempts, or server-side throttling.

Safe tests should:

- run manually
- use one request at a time
- avoid rapid retries
- record exact time, URL, headers, and result

### 4.8 Browser Session Or Proxy State

Browser success may depend on state that should not become part of the collector.

Forbidden materials include:

```txt
cookies
CSRF tokens
session IDs
WAF tokens
authorization headers
browser-private request identifiers
```

If these are required, live collection remains blocked until a safer source path is identified.

## 5. Safe Tests To Run

All tests must keep SSL verification enabled and must use the local CA bundle when needed.

### 5.1 Compare Browser Request Headers

From browser DevTools, compare the successful request with terminal/Docker requests.

Record only non-secret values:

```txt
URL
method
query parameters
status
response content-type
accept
accept-language
accept-encoding
referer
origin
cache-control
pragma
sec-fetch-dest
sec-fetch-mode
sec-fetch-site
user-agent
connection behavior
timing
```

Do not copy or store cookies, tokens, session IDs, WAF material, or authorization headers.

### 5.2 Curl IPv4

Test host and Docker variants with:

```bash
curl --ipv4 --http1.1 --fail --show-error -L \
  --connect-timeout 20 \
  --max-time 60 \
  --cacert certs/bvc-ca-bundle.pem \
  -H "accept: application/vnd.api+json" \
  -H "referer: https://www.casablanca-bourse.com/fr/live-market/marche-actions-listing" \
  "https://api.casablanca-bourse.com/fr/api/bourse_data/last_market_watches/action?page%5Blimit%5D=50&page%5Boffset%5D=0"
```

Repeat for offset `50` only if offset `0` succeeds or produces a clearly different failure.

### 5.3 Curl Compression

Test:

```bash
curl --compressed --http1.1 --fail --show-error -L \
  --connect-timeout 20 \
  --max-time 60 \
  --cacert certs/bvc-ca-bundle.pem \
  -H "accept: application/vnd.api+json" \
  -H "referer: https://www.casablanca-bourse.com/fr/live-market/marche-actions-listing" \
  "https://api.casablanca-bourse.com/fr/api/bourse_data/last_market_watches/action?page%5Blimit%5D=50&page%5Boffset%5D=0"
```

Also test explicit `Accept-Encoding` values:

```txt
gzip, deflate, br
gzip, deflate
identity
```

### 5.4 Connection Close

Test:

```txt
Connection: close
```

This checks whether keep-alive or connection reuse is involved.

### 5.5 Non-Secret Browser-Like Headers

Only if copied from browser DevTools without private material, test non-secret headers such as:

```txt
Accept: application/vnd.api+json
Accept-Language: fr-FR,fr;q=0.9,en;q=0.8
Referer: https://www.casablanca-bourse.com/fr/live-market/marche-actions-listing
Origin: https://www.casablanca-bourse.com
Cache-Control: no-cache
Pragma: no-cache
Sec-Fetch-Dest: empty
Sec-Fetch-Mode: cors
Sec-Fetch-Site: same-origin
User-Agent: a normal configured browser-like user agent
```

These may be considered later for runtime code only if they are stable, non-secret, and necessary.

### 5.6 Direct Host vs Proxy Host

Compare:

```txt
https://api.casablanca-bourse.com/fr/api/bourse_data/last_market_watches/action
https://www.casablanca-bourse.com/api/proxy/fr/api/bourse_data/last_market_watches/action
```

Use the same query parameters and non-secret headers.

### 5.7 Host Machine vs Docker Container

Run equivalent tests from:

```txt
host terminal
Docker api container
```

Record differences in:

```txt
DNS resolution
TLS verification result
timeout behavior
response headers
first-byte timing
```

### 5.8 Python httpx With Same Headers

After a successful curl variant is found, reproduce it with Python/httpx using:

```txt
verify = BVC_PRICE_COLLECTOR_CA_BUNDLE_PATH
timeout = explicit finite timeout
follow_redirects = true
same non-secret headers
```

Do not add this to automated tests against the live network.

## 6. Forbidden Tests

Do not run or document collector fixes based on:

```txt
cookies
CSRF tokens
session IDs
WAF tokens
Authorization headers
private browser headers
curl -k
SSL verify=false
disabled certificate verification
rapid retries
brute-force endpoints or offsets
browser automation as the default collector path
committed CA bundle files
committed private request captures
```

If the only working request requires forbidden material, live collection remains blocked.

## 7. Acceptance Criteria

The live HTTP timeout blocker can be considered fixed only when all criteria below are met:

1. A Docker/httpx request succeeds with SSL verification enabled.
2. The request uses no cookies, CSRF tokens, session IDs, WAF tokens, or authorization headers.
3. The request uses only stable, non-secret headers.
4. Direct API or proxy API can retrieve page 1 and page 2.
5. Responses are valid JSON with expected row counts.
6. Pagination works with offset `0` and offset `50`.
7. The behavior is reproducible from Docker, not only from a host browser.
8. The collector can store raw JSON pages before normalization.
9. A manual `--collect-live` run can be considered safe to attempt after the collector includes `Accept-Language`.
10. Results are documented before scheduler work resumes.

## 8. Possible Runtime Code Changes Later

Runtime code may be changed later only if the investigation confirms a safe, stable cause.

Allowed future changes may include:

- adding stable non-secret headers to collector config defaults
- adding environment-configurable non-secret headers
- forcing HTTP/1.1 if httpx protocol behavior is confirmed as the issue
- setting `Connection: close` if connection reuse is confirmed as the issue
- adding a documented IPv4 preference only if container networking proves IPv6 is the issue and the implementation is safe
- improving timeout error messages with request URL, endpoint mode, and retry count

The confirmed safe runtime change is:

```txt
Add BVC_PRICE_COLLECTOR_ACCEPT_LANGUAGE with default fr-FR,fr;q=0.9,en;q=0.8
Send Accept-Language on BVC JSON live requests
```

Runtime code must not:

- disable SSL verification
- hardcode cookies or tokens
- depend on browser session state
- add rapid retry behavior
- add browser automation as the default collector

## 9. Blocked Until Fixed

The following remain blocked:

```txt
docker compose run --rm api python -m tradehub_data.pipelines.bvc_prices.runner --collect-live
scheduler/workers
scheduled BVC live collection
production live collection approval
```

TradeHub integration is not part of this investigation and remains out of scope for this phase.

## 10. Codex Checklist

When investigating this blocker, Codex should:

1. Read `AGENTS.md`.
2. Read `docs/10_BVC_LIVE_MULTIPAGE_COLLECTOR.md`.
3. Read `docs/11_BVC_SSL_AND_LIVE_RUN_VALIDATION.md`.
4. Read this document.
5. Inspect current BVC collector config/client code.
6. Verify local CA bundle presence without committing it.
7. Keep SSL verification enabled.
8. Run only explicit, low-frequency manual HTTP tests.
9. Compare browser and terminal headers without copying secrets.
10. Record exact URL, headers tested, timeout/result, and environment.
11. Do not modify runtime code unless a safe cause is confirmed and the user explicitly requests implementation.
12. Do not run `--collect-live` until page 1 and page 2 are reproducibly retrievable from Docker/httpx.
13. Keep scheduler blocked until acceptance criteria are met.
