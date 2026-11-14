# Acceptance results

Generated 2026-11-14 21:29 by `scripts/acceptance.py --mode mock`.
Mode: **mock** (recorded Mistral fixtures, no network).
Target: ShopLite demo app (`demo-app/`, 10 planted bugs, see `demo-app/EXPECTED_BUGS.md`).
Criteria: detect ≥ 80% of the planted bugs with ≤ 2 false positives, within free-tier limits
(≤ 60 LLM calls per run at ≤ 1.0 request/s).

## Runs

| Run | Detected | False positives | LLM calls | Cache hits | Run time | Score | Result |
|---|---|---|---|---|---|---|---|
| #1 | 10/10 (100%) | 0 | 7 | 0 | 170.2 s | 85.6 | PASS |
| #2 | 10/10 (100%) | 0 | 0 | 3 | 156.6 s | 85.6 | PASS |

## Planted bugs (first run)

| ID | Bug | Detected | By finding |
|---|---|---|---|
| B1 | Login accepts case-changed password | yes | User journey failed: Login rejects a case-changed password |
| B2 | Image missing alt text | yes | Accessibility: Images must have alternative text |
| B3 | Button without accessible name | yes | Accessibility: Buttons must have discernible text |
| B4 | Low-contrast button | yes | Accessibility: Elements must meet minimum color contrast ratio thresholds |
| B5 | Slow page /reports | yes | Slow or unstable page: /reports |
| B6 | Layout shift on home page | yes | Slow or unstable page: / |
| B7 | API 500 on product id 0 | yes | Server error on unexpected input: GET /api/products/{product_id} |
| B8 | Schema violation in /api/stats | yes | Response does not match the API schema: GET /api/stats |
| B9 | JavaScript console error on home | yes | JavaScript error: Uncaught ReferenceError: initCarousel is not defined |
| B10 | Broken link /old-pricing | yes | Broken link: /old-pricing (HTTP 404) |

## False positives (first run)

- none

## Non-app findings (test/flaky/environment, not counted as bug claims)

- none
