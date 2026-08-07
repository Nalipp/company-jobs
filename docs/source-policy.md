# Source and Evidence Policy

## Source hierarchy

Use sources in two classes.

### Lead sources

Search engines, Google Jobs, LinkedIn, Indeed, Wellfound, Built In, and similar job
boards are useful for discovering company names and possible openings. Their facts
are preliminary.

### Canonical sources

An employer-controlled careers page, the employer's ATS board, or a primary
platform-hosted posting authored for that employer is authoritative for whether a
job is currently open and for its title, location, workplace arrangement,
employment type, salary, seniority evidence, responsibilities, and application
URL.

A platform posting is canonical only when it is the primary public listing, is
clearly attributable to the hiring company, and provides a direct way to apply.
Examples can include a company-authored Y Combinator Work at a Startup page. An
aggregator copy remains a lead source.

Common ATS providers include Ashby, Greenhouse, Lever, and Workday. Prefer a
provider's structured public job feed when it is accessible, but verify any unclear
field on the human-readable official posting.

## Admission rule

Never add a company from a lead source alone. At least one canonical job must be:

- accessible and active;
- attributable to the company;
- located at a canonical employer, ATS, or qualifying primary platform URL; and
- supported for every hard constraint by the official source.

If the canonical source conflicts with a lead source, use the canonical source.

## Active status

Treat a job as active when the official board currently lists it or its official
posting is accessible as an open role. A search-engine snippet, crawl date, cached
page, or third-party repost does not prove current activity.

If an official page says the job is closed, removed, filled, or unavailable, do not
admit it. If access is blocked, preserve the uncertainty in the final run summary
but do not treat the job as active.

## URLs

Save the canonical posting URL that a user can open to inspect or apply. Remove
obvious tracking parameters when that does not change the destination. Preserve a
query parameter or fragment when it distinguishes one job from another. Do not save
search-result URLs, redirect wrappers, or third-party application mirrors as the
canonical job URL.

Save the company's official board or careers landing page as `jobs_url`. Record an
ATS provider and board ID when they can be established without guessing.

## Evidence quality

Every saved job includes concise evidence for:

- active status;
- location;
- workplace arrangement;
- compensation;
- employment type;
- seniority; and
- role fit, including role-specific AI work.

Use exact salary text. For other gates, use a short official excerpt or a faithful
paraphrase. Evidence should make the decision inspectable without copying an entire
job description.

## Failure handling

A blocked, rate-limited, unsupported, or malformed source means the source could
not be evaluated. It never means the company has zero jobs.

Try a reasonable canonical alternative when one exists. If none succeeds, omit the
company or job change and add a concise issue to the handoff. Do not put the
uncertain record in the catalog.
