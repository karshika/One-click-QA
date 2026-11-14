# ShopLite: planted bugs

ShopLite is the demo target for OneClickQA. It has **10 planted bugs**. Apart from these
bugs the app is meant to be clean: labelled forms, `lang`, titles, good contrast, fast
pages and correct API status codes. That way any other finding counts as a **false
positive**.

Demo credentials (configure them on the project): `demo@example.com` / `Demo-Pass-2024`.

| ID | Category | Where | Bug | How OneClickQA should detect it |
|----|----------|-------|-----|----------------------------------|
| B1 | UI (login flaw) | `POST /login` | The password check is case-insensitive, so `dEMO-pASS-2024` logs you in | A negative login journey with a case-changed password fails its "stays on sign-in / shows error" assertion |
| B2 | Accessibility | `/` | Team image has no `alt` | axe `image-alt` |
| B3 | Accessibility | `/` | Icon-only "share" button has no accessible name | axe `button-name` |
| B4 | Accessibility | `/` | "Subscribe" button text is low contrast (#c4c4c4 on #fff) | axe `color-contrast` |
| B5 | Performance | `/reports` | 3 s artificial server delay | TTFB/LCP poor on `/reports` |
| B6 | Performance | `/` | Promo banner injected 700 ms after load without reserved space | CLS > 0.1 on `/` |
| B7 | API | `GET /api/products/{id}` | `id=0` raises ZeroDivisionError, which returns 500 | Boundary input check returns 5xx |
| B8 | API | `GET /api/stats` | `total_orders` is a string; the OpenAPI schema says integer | Response schema validation fails |
| B9 | UI (JS error) | `/` | `initCarousel is not defined` thrown on load | Console/page error captured during crawl |
| B10 | UI (broken link) | nav, Pricing | Links to `/old-pricing`, which returns 404 | Crawl records a 404 for a linked page |

Acceptance target: at least 8 of 10 detected (80%), at most 2 false positives.

Matchers in `expected_bugs.json` are used by `scripts/acceptance.py` to score a run
automatically. Each finding is matched against the bug list. A finding that matches no
planted bug counts as a false positive.
