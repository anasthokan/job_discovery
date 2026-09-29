# E-Verify Employer Search API

Give this document to the frontend developer for the **E-Verify Checker** screen (`/tools/everify`).

**Production base URL:** `http://74.208.184.175:517`

Local example: `http://127.0.0.1:9001`

Interactive try-out: [http://74.208.184.175:517/docs](http://74.208.184.175:517/docs)

CORS is open (`allow_origins=["*"]`).

The Search button calls one endpoint. Dropdown values come from `/api/everify/filters`.

| UI | Control | API |
| --- | --- | --- |
| Employer Legal Name or Company Keyword | text, required | query `company` |
| State Location | dropdown | query `state` |
| Industry Category | dropdown | query `industry` |
| Search E-Verify Database | button | `GET /api/everify/search` |

## Dropdowns

```js
const res = await fetch("http://74.208.184.175:517/api/everify/filters");
const data = await res.json();
// data.states[].value / data.states[].label
// data.industries[].value / data.industries[].label
```

Send `value`, not the visible label. The full label is also accepted.

| State Location label | `state` |
| --- | --- |
| All US States & Territories | `All` |
| California (SF Bay Area & LA) | `California` |
| Washington (Seattle / Redmond) | `Washington` |
| New York (NYC Metro) | `New York` |
| Texas (Austin / Dallas) | `Texas` |
| Massachusetts (Boston / Cambridge) | `Massachusetts` |

| Industry Category label | `industry` |
| --- | --- |
| All Industries | `All` |
| Software, Cloud & AI Tech | `software` |
| Fintech & Financial Services | `fintech` |
| Healthtech & Biotech | `healthtech` |
| Defense & Aerospace | `defense` |

Industry is accepted and echoed. The public E-Verify employer list has no industry column, so changing Industry Category does not remove rows. Read `industry_applied` (always `false`) and `industry_note`.

State filters **Hiring Site Locations**. A row is kept when that field contains the state code (`CA`, `WA`, `NY`, `TX`, `MA`) or the state name. `All` does not filter.

## Search

`company` is required, minimum 2 characters. Match is against employer legal name and Doing Business As.

```bash
curl "{BASE_URL}/api/everify/search?company=Google&state=California&industry=software"
```

```js
const company = document.getElementById("everify-company").value.trim();
const state = document.getElementById("everify-state").value;       // All | California | ...
const industry = document.getElementById("everify-industry").value; // All | software | fintech | healthtech | defense

const params = new URLSearchParams({
  company,
  state,
  industry,
  limit: "25",
  offset: "0",
});
const res = await fetch(`http://74.208.184.175:517/api/everify/search?${params}`);
const data = await res.json();
if (!res.ok) throw new Error(data.detail || "E-Verify search failed");
```

Query:

| Query | Default | Notes |
| --- | --- | --- |
| `company` | required | Legal name or keyword. Min 2 characters |
| `state` | `All` | See table above |
| `industry` | `All` | See table above. Does not filter |
| `limit` | `25` | Max **100** |
| `offset` | `0` | Skip this many matches |

Response:

```json
{
  "ok": true,
  "company": "Google",
  "state": "California",
  "state_label": "California (SF Bay Area & LA)",
  "industry": "software",
  "industry_label": "Software, Cloud & AI Tech",
  "industry_applied": false,
  "industry_note": "The E-Verify employer list has no industry column, so Industry Category does not narrow these results.",
  "database_ready": true,
  "count": 1,
  "total": 1,
  "limit": 25,
  "offset": 0,
  "stem_opt_24_month": "eligible",
  "message": "At least one matched employer is enrolled in E-Verify. That meets the employer requirement for a 24-month STEM OPT extension.",
  "results": [
    {
      "employer": "Google LLC",
      "dba": null,
      "account_status": "Open",
      "enrolled": true,
      "e_verify_plus": false,
      "date_enrolled": "9/8/2008",
      "hiring_sites": "CA,WA,NY,TX,MA",
      "hiring_site_states": ["CA", "MA", "NY", "TX", "WA"],
      "mou_registered": true,
      "stem_opt_24_month": "eligible"
    }
  ]
}
```

Show `data.message` under the button. It already covers no match, not loaded, eligible, and not enrolled.

## What to render per result

| Field | UI meaning |
| --- | --- |
| `employer` | Employer legal name |
| `dba` | Doing Business As. Hide the row when `null` |
| `account_status` | `Open` or `Terminated` |
| `enrolled` | `true` when the E-Verify account is Open |
| `mou_registered` | Same as `enrolled`. Enrollment is the MOU with DHS |
| `date_enrolled` | MOU / enrollment date. Hide when `null` |
| `e_verify_plus` | `true` / `false` / `null`. Label: Opted into E-Verify+ |
| `hiring_sites` | Hiring site locations, usually state codes |
| `hiring_site_states` | Parsed state codes, for chips |
| `stem_opt_24_month` | `eligible`, `not_eligible`, or `unknown` |

`stem_opt_24_month` on the **top level** is the banner for the whole search. On each result it is that employer only.

| Value | Meaning |
| --- | --- |
| `eligible` | Account is Open. This meets the **employer** requirement for a 24-month STEM OPT extension |
| `not_eligible` | Account is not Open (usually Terminated) |
| `unknown` | No matching enrollment. This is not a confirmed No |

This flag does not say a student or a degree qualifies. It only reflects employer E-Verify enrollment.

Empty states:

| Condition | What to show |
| --- | --- |
| `database_ready: false` | Employer list is not loaded. Do not treat this as "company is not enrolled" |
| `total: 0` and `database_ready: true` | No name match for this keyword and state. Show `message` |
| `res.ok` is false | Show `detail` |

## Errors

| Status | When |
| --- | --- |
| 422 | `company` missing or shorter than 2 characters |
| 400 | `state` or `industry` is not one of the values above. `detail` lists the allowed values |

## Single company check

Jobs listings still use `e_verified` on each job (`JOBS_API.md`). To check one name without the state dropdown:

```bash
curl "{BASE_URL}/api/everify/lookup?company=Microsoft"
```

```json
{
  "ok": true,
  "company": "Microsoft",
  "e_verified": "yes",
  "e_verify_name": "Microsoft Corporation",
  "e_verify_status": "Open",
  "e_verify_plus": "No",
  "enrolled": true,
  "mou_registered": true,
  "date_enrolled": "6/1/2008",
  "hiring_sites": "WA,CA",
  "stem_opt_24_month": "eligible"
}
```

`e_verified: "unknown"` means the name did not match. It is not a confirmed No.

The Checker screen should call `/api/everify/search`, not `/api/everify/lookup`.
