# Website Technology Detector

A small Python CLI that identifies publicly observable website technologies and explains every detection.

The project includes **60 technology fingerprints**. Chromium executes the homepage's JavaScript while the scanner records rendered HTML, resource URLs, network activity, headers, metadata, cookie names, selected JavaScript globals, and DOM property names. A generic matcher applies JSON rules to these observations. Every result includes the observed value, its source, the exact matching text, and the rule that matched it.

There is no database or service to deploy. The CLI writes a JSON report and prints a short summary grouped by category.

## Installation

Requires Python 3.10 or newer. From the project directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe main.py https://example.com
```

The Windows commands above do not require virtual environment activation. For the shorter `python` commands below, first run `.\.venv\Scripts\Activate.ps1`, or continue using `.\.venv\Scripts\python.exe` in place of `python`. On macOS/Linux, activate with `source .venv/bin/activate`, then run `python -m pip install -r requirements.txt` and `python -m playwright install chromium`. Linux environments may need `python -m playwright install --with-deps chromium`.

If scanning reports that Chromium's executable does not exist, run `.\.venv\Scripts\python.exe -m playwright install chromium` again. Installing the Python package does not install the browser. Use the same Python environment for both installation and scanning, and reinstall the browser after a Playwright upgrade if its required browser revision changes.

Verified on Windows with Python 3.13.2, Playwright 1.63.0, BeautifulSoup 4.15.0, and Pytest 9.1.1. Dependency ranges keep the setup small; an exact dependency lock would be useful for a longer-lived project.

## Usage

```powershell
python main.py https://www.allbirds.com
python main.py example.com --output reports/example.json --verbose
python main.py https://example.com --timeout 15 --wait 5
python main.py http://localhost:8000 --headed
python main.py https://example.com --fingerprints custom-fingerprints.json
python main.py --help
```

A bare hostname uses HTTPS. URLs with credentials and non-HTTP(S) schemes are rejected. `--output` defaults to `report.json`, overwrites that file, and creates missing parent directories. `--verbose` prints all evidence and matching rules; the default shows one evidence item per technology. The JSON report always includes all distinct evidence.

The default navigation timeout is 20 seconds, followed by a 2.5-second observation window. `--timeout` accepts up to 120 seconds and `--wait` accepts 0–30 seconds. These are separate budgets, not a single wall-clock deadline; browser startup and signal extraction add some overhead.

Exit codes: **0** for a completed scan, including partial results or no matches; **1** for invalid input, browser/navigation failures, invalid fingerprints, or output errors; **130** for interruption. Partial scans and HTTP error pages have explicit warnings in the CLI and report. A fatal failure does not write a new report, so an existing output file can remain from an earlier scan.

### Example output

This is a shortened version of a live Allbirds homepage scan on October 12, 2026. Results can change with the website, region, cookies, or observation window.

```text
Website: https://www.allbirds.com/
HTTP: 200 | Detected: 7 technologies
Warning: DOM property inspection was limited to the first 2000 elements.

Analytics
  Elevar (5 evidence items)
    Script URL: https://shopify-gtm-suite.getelevar.com/configs/f36f1dd17dff728b044638bc4ad9f1579fa7cda1/config.js
  Google Tag Manager (4 evidence items)
    Script URL: https://www.googletagmanager.com/gtm.js?id=GTM-TH8KRSBJ
  Microsoft Clarity (4 evidence items)
    Script URL: https://www.clarity.ms/tag/yrlg0i3snv?ref=shopify

E-commerce
  Shopify (43 evidence items)
    Script URL: https://cdn.shopify.com/s/files/1/1104/4168/files/jsEncrypt.js?v=1756910700

Infrastructure
  Cloudflare (2 evidence items)
    HTTP header [server]: cloudflare

JavaScript
  React (1 evidence item)
    DOM property: __reactContainer$0mc2vx4l7i9m
  Vue (1 evidence item)
    DOM property: __vue_app__
```

For a simpler evidence example, the controlled HTML fixture produces this Klaviyo result:

```json
{
  "name": "Klaviyo",
  "category": "Marketing Automation",
  "evidence": [
    {
      "signal": "scripts",
      "key": null,
      "value": "https://static.klaviyo.com/onsite/js/klaviyo.js?company_id=TEST",
      "matched_text": "https://static.klaviyo.com/onsite/",
      "source_url": "https://store.example/",
      "rule": {
        "id": "klaviyo-1",
        "signal": "scripts",
        "pattern": "^https?://(?:[a-z0-9-]+\\.)*static\\.klaviyo\\.com(?::\\d+)?/onsite/"
      }
    }
  ]
}
```

The full report also contains `schema_version`, requested and final URLs, a UTC scan timestamp, HTTP status, page title, warnings, signal counts, and the number of loaded fingerprints. HTML evidence contains a matching excerpt with up to 80 characters of context on each side and a `match_span` into the rendered HTML snapshot. Other evidence retains the complete observed URL, header/meta value, cookie name, or property name. Cookie evidence includes its domain and path. Network evidence includes the resource type.

## Architecture

```text
main.py            CLI arguments, readable output, JSON report writing
scanner.py         Chromium lifecycle and observation collection; HTML parsing
detector.py        Fingerprint validation, regex matching, evidence and reports
fingerprints.json  Technology names, categories, rules, selected source references
tests/             Controlled HTML fixture, matcher/CLI tests, browser tests
requirements.txt   Playwright, BeautifulSoup, Pytest
```

1. `Detector` loads and validates the fingerprints, compiles the regexes once, and gathers the JavaScript property paths to inspect.
2. `scan_website()` opens a fresh browser context, attaches request/response listeners **before navigation**, follows redirects, and waits for `DOMContentLoaded`.
3. A bounded observation window allows delayed tags and requests to appear. The scanner then parses the rendered DOM with BeautifulSoup, reads the final main-document response headers, and checks configured globals and DOM property names.
4. The detector evaluates each rule against the corresponding observations and creates evidence for each distinct match.
5. The CLI prints the results in category/name order and saves the complete report.

`Observation` and `ScanResult` are simple dataclasses shared by the scanner and detector. `Detector` is the only stateful matching component. There are no technology-specific branches in the Python matcher. The scanner discovers DOM property names generically, which allows React and Vue detection even when bundling removes their global variables.

## Fingerprint rules

The JSON file has `schema_version: 1` and a `technologies` list. Each technology has a `name`, a `category`, and one or more `rules`. Optional `sources` record useful vendor or implementation references.

| Rule field | Meaning |
| --- | --- |
| `id` | Required, globally unique stable identifier, included in evidence |
| `signal` | Which observation collection to inspect |
| `pattern` | Required Python regular expression applied to the observation's value |
| `key` | Optional exact selector for a named header, meta tag, cookie, or global; required for globals |
| `case_sensitive` | Optional boolean; defaults to `false` |

| Signal | Observed value | Key, if applicable |
| --- | --- | --- |
| `html` | Rendered HTML snapshot | None |
| `headers` | Final main-document HTTP response header value | Lowercase header name |
| `scripts` | Absolute script URL, from requests and DOM tags | None |
| `stylesheets` | Absolute stylesheet URL, from requests and DOM tags | None |
| `meta` | Meta content | Lowercase name, property, or http-equiv |
| `cookies` | Cookie name only | Same cookie name |
| `network` | Request URL, including fetch/XHR and iframe requests | None |
| `globals` | Literal `present` | Dotted path relative to `window`, e.g. `Shopify.shop` |
| `dom` | Enumerable property name observed on a document/element | None |

Rules use OR semantics: any rule can establish a detection. Each rule must therefore be independently specific enough. There are no confidence scores, technology implications, or guesses about versions. Header/meta keys are compared without case; JavaScript property paths are case-sensitive even when the value regex is case-insensitive.

A global path is walked as properties without calling its functions or evaluating fingerprint text as JavaScript. Its value is never serialized. DOM property **names** are inspected without serializing their values. Only the first 2,000 elements are inspected for DOM properties, and network observations are limited to 2,000 distinct requests; reaching either limit produces a warning.

Repeated observations are deduplicated by signal, source, key, value, and identifying details. The detector also includes the rule ID in its deduplication key. A URL observed both as a script and a network request remains two distinct types of evidence. Two cookies with the same name on different domains remain distinct.

### Adding a technology

Add an object to `technologies`, for example:

```json
{
  "name": "Example Analytics",
  "category": "Analytics",
  "rules": [
    {
      "id": "example-analytics-loader",
      "signal": "scripts",
      "pattern": "^https?://cdn\\.example-analytics\\.com/loader\\.js(?:[?#]|$)"
    },
    {
      "id": "example-analytics-global",
      "signal": "globals",
      "key": "ExampleAnalytics.track",
      "pattern": "^present$"
    }
  ]
}
```

Use a documented script endpoint, distinctive property, or vendor-specific marker. Anchor URL rules to the actual host and path, so a vendor name in a blog post, query string, or lookalike hostname cannot establish a match. Avoid generic class names, shared globals such as `dataLayer`, or generic session cookies. For example, an `AW-` Google Ads tag does not identify Google Analytics; its `gtag.js` rule requires a `G-` or `UA-` ID.

Add a positive fixture and a realistic negative example to the tests, then run them. Invalid regexes, unknown signals, missing fields, duplicate names/IDs, and malformed global paths are rejected when the file loads. Custom fingerprints are trusted local configuration; this is not a sandbox for arbitrary untrusted regexes.

## Technology coverage

The 60 entries prioritize commonly observable software, including Shopify's competing analytics integrations:

| Category | Technologies |
| --- | --- |
| E-commerce | Shopify, WooCommerce, Magento, BigCommerce, PrestaShop |
| Website Builders | Squarespace, Wix, Webflow |
| CMS | WordPress, Drupal, Joomla, Ghost |
| JavaScript | React, Next.js, Vue, Nuxt, Angular, jQuery, Alpine.js, SvelteKit, Ember.js, Lodash, GSAP |
| UI Libraries | Bootstrap, Font Awesome |
| Analytics | Google Analytics, Google Tag Manager, Hotjar, Microsoft Clarity, Segment, Mixpanel, Amplitude, Plausible, Matomo, Heap, PostHog, Adobe Analytics, Lucky Orange, Triple Whale, Northbeam, Elevar |
| Marketing Automation | Klaviyo, HubSpot, Mailchimp, ActiveCampaign, Omnisend |
| Advertising | Meta Pixel, TikTok Pixel, Pinterest Tag |
| Customer Support | Intercom, Zendesk, Tawk.to, Crisp |
| Infrastructure | Cloudflare, Nginx, Apache, Vercel, Netlify |
| Consent Management | OneTrust, Cookiebot |

Coverage is a list of supported signatures, not a measured recall rate. These signatures cover selected installation variants. A technology that leaves no matching public signal will be missed. AngularJS is not inferred from an `angular.js` filename as modern Angular.

### Dataset provenance

The bundled fingerprints were authored for this project; no external fingerprint dataset or detection engine is bundled. Selected rules include links to primary references, such as [Triple Whale's installation guide](https://kb.triplewhale.com/en/articles/6266255-how-to-add-the-triple-pixel-to-a-headless-business-or-custom-website), [Lucky Orange's browser documentation](https://developers.luckyorange.com/libraries/browser), [Elevar's debugging API](https://dev.getelevar.com/guides/api/browser-setup/debug-mode), and [React's DOM implementation](https://github.com/facebook/react/blob/main/packages/react-dom-bindings/src/client/ReactDOMComponentTree.js).

I considered importing a larger dataset. The community-maintained [WebAppAnalyzer repository](https://github.com/enthec/webappanalyzer) identifies its license as GPL-3.0. Its format also supports concepts beyond this assignment's simple rule model. I chose a smaller authored catalog to keep provenance, behavior, and maintenance easy to review. A future import should pin a dataset revision, preserve the required notices, review its license requirements, and explicitly translate supported rule semantics into this engine.

## Tests

```powershell
python -m pytest -q
python -m pytest -q --run-browser
```

The first command runs unit tests without opening a browser; browser tests are explicitly skipped. The second additionally requires installed Chromium and runs the full suite. Browser tests use a local HTTP server and synthetic scripts; they never contact third-party vendors.

The suite covers all signal types through representative fingerprints, actual evidence and HTML match offsets, duplicate handling, cookie domains, missing/invalid observations, malformed rules and URLs, misleading text/URLs, CLI report/error behavior, and the small HTML fixture. Browser tests verify delayed scripts, dynamic styles and metadata, globals, React-like DOM properties, fetch requests, redirects, response headers, cookie privacy, navigation timeouts, HTTP errors, and fatal navigation failures. Live scans are useful smoke checks but are excluded from regression tests because websites and network conditions change.

## Engineering tradeoffs and limitations

- **Observable evidence versus actual use:** a script tag or request proves that code was referenced or requested; it does not prove successful execution, an active subscription, or collection of events. A global can be a bootstrap queue. The report exposes the kind of proof instead of inventing confidence percentages.
- **Backend visibility:** databases, server-side analytics, private Shopify apps, and backend frameworks may leave no public signal. Exposed headers can identify some infrastructure, but headers can also be removed or spoofed. No absence-of-match claim means a technology is absent.
- **False positives:** anchored hosts, specific paths and keys, and negative tests reduce accidental matches. Copied assets, stale tags, shared libraries, or deliberately spoofed markers can still mislead a passive scanner. A report may identify technologies used by an embedded widget rather than the entire storefront. There is no measured precision/recall benchmark yet.
- **JavaScript timing and consent:** browser execution catches dynamic signals, but a fixed observation window misses late activity. Consent gates, login, geography, bot protection, and interactions change what loads. The scanner does not click consent banners, scroll, submit forms, or bypass challenges. `--wait` and `--headed` help inspect these cases.
- **Navigation strategy:** `DOMContentLoaded` plus a short observation window is predictable on sites with continuous analytics traffic. Waiting for complete network idleness can stall; Playwright also [discourages relying on `networkidle`](https://playwright.dev/python/docs/api/class-page#page-goto). Navigation timeouts preserve usable observations after a document response, while failed navigation produces an actionable error. HTTP 4xx/5xx pages are reported with warnings rather than mistaken for healthy storefronts.
- **Homepage-only analysis:** one representative homepage is a deliberate cost/scope choice. Product or cart pages can reveal extra integrations, but automated checkout interaction risks side effects. The scanner sees iframe requests, but HTML, globals, and DOM inspection cover the main document only; it does not inspect every frame, shadow root, worker, or external JavaScript body.
- **Report data:** cookie values, `Set-Cookie`, request bodies, and JavaScript object values are not reported. Raw HTML is held in memory for matching and only matching excerpts are exported. Complete observed URLs and HTML snippets are preserved for evidence, so their query parameters or surrounding content may contain identifiers. Reports are not fully anonymized.
- **Signature maintenance:** vendors change domains, filenames, globals, and deployment patterns. The plain JSON catalog is easy to review but must be maintained with documented examples, positive/negative fixtures, and occasional live checks. Minified bundles and first-party proxying can hide recognizable signatures.
- **Simple rule semantics:** OR rules and the first regex match per rule/observation keep the implementation understandable. Composite AND conditions, exclusions, version extraction, and richer DOM selectors could improve ambiguous detections, but would also expand the schema and test burden.

## Debate topics and future improvements

### What are the main issues, and how would I tackle them?

- **Incomplete coverage:** the homepage, fixed observation window, and main-document inspection can miss integrations on product pages, delayed tags, and sandboxed Shopify pixels. I would first add a bounded same-origin visit to one product page and the cart page, inspect relevant frames, and sample signals during a capped observation window. Each observation would retain its page/frame source. Consent-gated or backend-only technologies would remain explicit visibility limitations.
- **Unmeasured accuracy:** copied assets, stale tags, bootstrap queues, and spoofed headers can produce misleading matches. The current tests verify behavior, but do not establish precision or recall across real websites. I would build a labeled sample of known installations and realistic negatives, measure errors by technology and signal, and tighten the weakest signatures. Ambiguous technologies may justify composite rules later; a requested script would still be described as that kind of evidence rather than proof of successful initialization.
- **Browser cost and operational limits:** launching Chromium for every domain is expensive, and navigation timeouts do not create a single deadline for the entire scan. I would benchmark runtime, memory, bandwidth, and failure rates on a representative sample, then introduce a total scan budget, bounded browser concurrency, and browser reuse with isolated contexts. Keeping these changes driven by measurements would prevent unnecessary infrastructure.

### How would I crawl millions of domains within 1–2 months?

I would begin with the workload and required completion rate. For example, **10 million domains require about 333,333 completed domains per day over 30 days, or 166,667 per day over 60 days**: approximately **3.86 or 1.93 successful domain scans per second**. These are completion targets, not individual HTTP request rates; each browser visit can issue many requests.

As an illustrative capacity calculation, if every scan took 10 seconds, the ideal minimum would be approximately 39 concurrent scans for 30 days or 20 for 60 days. That excludes retries, extra pages, downtime, and slow sites. The actual worker count would come from a pilot measuring scan duration, the fraction needing a browser, CPU/RAM per browser, bandwidth, and failure rates. These numbers are planning assumptions, not measured performance of this implementation.

I would use a cheap HTTP pass to match headers and static markup, then send domains needing fuller coverage to a bounded browser pool. To measure what the cheap pass misses, I would also browser-scan a random sample of domains that already matched static rules. The browser selection policy is a cost-versus-recall decision: even a recognizable storefront can have additional analytics injected by JavaScript.

At that scale, I would partition the domain list across workers, checkpoint progress in durable storage, and use a work queue so failed workers can resume without repeating completed scans. Normalize and deduplicate domains, cap retries with backoff, apply per-host rate limits, and record blocked, failed, and partial scans separately from successful scans with no matches. Track daily completion rate and remaining work against the deadline, and increase capacity only within CPU, memory, bandwidth, and budget limits. Cache recent results and rescan changed or stale domains incrementally. A hosted version would also need URL/redirect validation and network isolation before scanning arbitrary input.

These are proposals for a future batch system; the submitted implementation remains a local CLI.

### How would I discover new technologies in the future?

I would start with recurring **unmatched script hosts, distinctive asset paths, generator metadata, and DOM markers** across representative scans. Grouping these unknown signals would help identify gaps and prioritize technologies relevant to Shopify analytics. Vendor installation guides, public SDK source, package releases, Shopify app listings, and changelogs would provide candidate signatures. A vendor name or app listing alone would not establish that a scanned site uses it.

For each candidate, I would confirm a distinctive observable marker on a known installation, check additional unrelated installations when available, and compare against sites without the technology. I would then add a reviewed JSON entry with stable rule IDs, its category, and source references, plus positive and negative fixtures. Rules would be checked for lookalike hosts, generic strings, and shared libraries before being added to the catalog. Any imported dataset would require a license review and a pinned revision, as discussed under dataset provenance.

Discovery also includes maintenance: periodically recheck known installations, track signature changes, and revise or retire rules that stop matching or create false positives. Automated clustering can suggest candidates, but human review should confirm their meaning before the tool reports a new technology.
