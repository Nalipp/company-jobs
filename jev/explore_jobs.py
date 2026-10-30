"""Evaluate reusable job/company features with Jev and filter the results.

The V5 database is read-only.  This script stores only derived Jev answers in
jev_cache.sqlite so that changing filters does not require re-running Jev.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_DIR = Path(__file__).resolve().parent
DATABASE_PATH = PROJECT_DIR.parent / "_v5_daily-jobs" / "data" / "v5.sqlite"
CACHE_PATH = PROJECT_DIR / "jev_cache.sqlite"
TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"

MAX_JOB_DESCRIPTION_CHARS = 16_000
MAX_JOB_DETAILS_CHARS = 6_000
MAX_COMPANY_JOB_EVIDENCE = 20
MAX_COMPANY_JOB_DESCRIPTION_CHARS = 1_200
PROGRESS_EVERY = 100

# These features are intentionally left out of the broad job pass for now.
# Salary buckets are better handled deterministically from compensation data, and
# company.origin.south_korea needs richer company evidence than the current
# bounded company state provides.
DEFERRED_FEATURE_KEYS = frozenset(
    {
        "company.origin.south_korea",
        "job.salary.under_200k",
        "job.salary.200k_300k",
        "job.salary.300k_400k",
        "job.salary.over_400k",
    }
)


@dataclass(frozen=True)
class Feature:
    key: str
    scope: str
    version: int
    instructions: str
    criteria: dict[str, str]


# Add new questions here.  Keep each feature independent so its cached result
# can be reused when the active filters change.
FEATURES: dict[str, Feature] = {
    "job.work.research_ux_ui": Feature(
        key="job.work.research_ux_ui",
        scope="job",
        version=1,
        instructions=(
            "Does this software engineering role include meaningful responsibility "
            "for exploring user or product problems through research, discovery, "
            "or experimental software prototypes? Require hands-on software "
            "development plus concrete work investigating user needs or undefined "
            "product problems, conducting or collaborating directly in user "
            "research or usability testing, or building working prototypes to test "
            "ideas and iterating based on user feedback. Comfort with ambiguity, "
            "design collaboration, rapid experimentation, and taking prototypes "
            "toward production support a match when connected to these duties; "
            "generic customer-focused or ambiguity language alone is insufficient. "
            "Exclude research mentioned only in degree requirements, academic "
            "credentials, publications, or scientific/model research without "
            "user-facing product exploration. Pure design or user-research roles "
            "without substantial software development do not qualify."
        ),
        criteria={
            "true": (
                "The role combines hands-on software development with explicit "
                "user or product discovery, user research, usability testing, or "
                "working prototypes used to investigate and validate product ideas."
            ),
            "false": (
                "There is insufficient evidence of software development combined "
                "with research-driven product exploration; the posting describes "
                "only predefined implementation, generic ambiguity, purely "
                "non-engineering research/design, or academic/scientific research "
                "without that product responsibility."
            ),
        },
    ),
    "job.domain.education": Feature(
        key="job.domain.education",
        scope="job",
        version=1,
        instructions=(
            "Is teaching, learning, or skill development a central purpose of "
            "this role's team, product, or substantial responsibilities? Count "
            "education-focused labs and products serving students, educators, "
            "schools, universities, or adult learners, including educational "
            "software, classroom tools, tutoring, personalized learning, "
            "assessment, practice, professional education, and skill development. "
            "Require role-specific evidence, such as an education-focused team "
            "mission or concrete responsibility for developing or testing "
            "learning experiences. This is a domain filter and may match any "
            "job function. Exclude incidental education mentions, degree "
            "requirements, tuition benefits, employee onboarding, routine internal "
            "training, or generic educating-customers language. Employment at "
            "a university or an AI company alone is insufficient."
        ),
        criteria={
            "true": (
                "The role's team, product, or substantial responsibilities "
                "explicitly center on teaching, learning, education products, "
                "or skill development, including an education-focused lab."
            ),
            "false": (
                "There is no role-specific teaching or learning purpose, or "
                "education appears only in qualifications, benefits, onboarding, "
                "routine training, employer identity, or incidental wording."
            ),
        },
    ),
    "job.location.san_francisco": Feature(
        key="job.location.san_francisco",
        scope="job",
        version=1,
        instructions=(
            "Is this job posting for a work location in San Francisco, "
            "California? Use posting_locations as direct location evidence when "
            "present. Do not treat an employer headquarters, office address, "
            "compensation market, or general company description as the job's "
            "location."
        ),
        criteria={
            "true": (
                "The posting lists San Francisco, California as a work location, "
                "including when it is one of several listed work locations."
            ),
            "false": (
                "The posting lists another work location, is remote without "
                "identifying San Francisco as the work location, or does not "
                "provide enough location evidence."
            ),
        },
    ),
    "job.workplace.onsite": Feature(
        key="job.workplace.onsite",
        scope="job",
        version=1,
        instructions=(
            "Does this job require the employee to work primarily or fully at an "
            "employer, client, laboratory, or other physical worksite? Use "
            "workplace_text and explicit requirements in the posting. Do not count "
            "optional office access, interviews, onboarding, occasional meetings, "
            "offsites, travel, or merely listing an office location."
        ),
        criteria={
            "true": (
                "The posting requires full-time or primarily in-person work at a "
                "physical worksite and does not describe the role as hybrid."
            ),
            "false": (
                "The role is remote, hybrid, optional-office, or the posting does "
                "not provide enough workplace evidence."
            ),
        },
    ),
    "job.workplace.hybrid": Feature(
        key="job.workplace.hybrid",
        scope="job",
        version=1,
        instructions=(
            "Does this job require a recurring combination of in-person and remote "
            "work? Count explicit hybrid arrangements or a required recurring office "
            "schedule, such as one to three days per week. Do not count remote jobs "
            "with only occasional travel or meetings, optional office access, or "
            "on-site jobs that merely allow occasional work from home."
        ),
        criteria={
            "true": (
                "The posting explicitly requires hybrid work or a recurring mix of "
                "remote work and in-person attendance."
            ),
            "false": (
                "The role is fully or primarily on-site, remote-only, offers office "
                "access only as an option, or lacks enough evidence."
            ),
        },
    ),
    "job.workplace.remote": Feature(
        key="job.workplace.remote",
        scope="job",
        version=1,
        instructions=(
            "Does this posting explicitly allow remote work as a regular workplace "
            "arrangement for this job? Remote work may be geographically restricted. "
            "Count fully remote, remote-first, remote-eligible, or jobs explicitly "
            "offering remote as one of several arrangements. Do not infer remote work "
            "from flexible hours, distributed teams, travel, or the absence of an "
            "office requirement."
        ),
        criteria={
            "true": (
                "The posting explicitly allows regular remote work, including a "
                "geographically restricted remote arrangement or remote as one of "
                "several stated workplace options."
            ),
            "false": (
                "The role requires primarily in-person or hybrid work, mentions "
                "only occasional remote work, or lacks enough evidence."
            ),
        },
    ),
    "job.role.frontend_engineering": Feature(
        key="job.role.frontend_engineering",
        scope="job",
        version=1,
        instructions=(
            "Is frontend engineering a substantial responsibility of this job? "
            "Count building browser-based or user-facing frontend applications, UI "
            "components, frontend architecture, design systems, accessibility, or "
            "frontend performance, including work with technologies such as JavaScript, "
            "TypeScript, React, Vue, or similar frameworks. Do not count incidental UI "
            "work in a role that is primarily backend, infrastructure, data, research, "
            "design, or product management."
        ),
        criteria={
            "true": (
                "The title or responsibilities show that frontend engineering is a "
                "substantial part of the role, including a genuinely full-stack role "
                "with meaningful frontend ownership."
            ),
            "false": (
                "Frontend work is absent, incidental, or limited to collaboration "
                "with frontend engineers rather than implementing frontend software."
            ),
        },
    ),
    "job.role.product_ux_design": Feature(
        key="job.role.product_ux_design",
        scope="job",
        version=1,
        instructions=(
            "Is product, UX, UI, interaction, or digital-interface design the primary "
            "purpose of this position? Count roles primarily responsible for user flows, "
            "interaction design, interface design, usability, information architecture, "
            "prototyping, design systems, or shaping a digital product experience. Allow "
            "light engineering work such as HTML/CSS, coded prototypes, design-system "
            "implementation, or design tooling when those activities support a primarily "
            "design-oriented role. Judge the responsibilities, not the title alone. Do not "
            "count software engineering roles that merely implement designs, product managers "
            "with occasional UX responsibilities, standalone user-research roles, or brand, "
            "marketing, graphic, and industrial-design roles."
        ),
        criteria={
            "true": (
                "Product, UX, UI, interaction, or digital-interface design is the role's "
                "primary responsibility. Limited coding or technical prototyping may be included."
            ),
            "false": (
                "Design is secondary to engineering, product management, research, marketing, "
                "or another function, or the role focuses on a different design discipline."
            ),
        },
    ),
    "job.role.technical": Feature(
        key="job.role.technical",
        scope="job",
        version=1,
        instructions=(
            "Are the primary responsibilities of this job hands-on technical work "
            "that requires specialized engineering, computing, data, scientific, or "
            "systems expertise? Count building, programming, analyzing, testing, "
            "debugging, deploying, securing, or operating software, data systems, "
            "machine-learning systems, infrastructure, or technical hardware. Judge "
            "the actual role, not whether the employer is a technology company or the "
            "role collaborates with technical teams."
        ),
        criteria={
            "true": (
                "The role's primary responsibilities involve hands-on engineering, "
                "programming, technical analysis, technical research, infrastructure, "
                "security, data, machine learning, or comparable specialized technical work."
            ),
            "false": (
                "The role is primarily product management, project management, design, "
                "sales, marketing, recruiting, customer success, finance, legal, operations, "
                "administration, or another business function without substantial hands-on "
                "technical responsibilities."
            ),
        },
    ),
    "job.role.software_engineering": Feature(
        key="job.role.software_engineering",
        scope="job",
        version=1,
        instructions=(
            "Is software engineering a primary responsibility of this job? Count roles "
            "that substantially design, write, review, test, debug, deploy, or operate "
            "software code, including frontend, backend, full-stack, mobile, platform, "
            "infrastructure, developer-tools, and software-focused machine-learning roles. "
            "Do not count a role merely because it works with engineers, uses technical "
            "software, writes occasional scripts, or has technical in the title."
        ),
        criteria={
            "true": (
                "A substantial part of the job is designing, implementing, testing, "
                "debugging, deploying, or operating software systems through code."
            ),
            "false": (
                "The role is primarily management, product, design, analysis, research, "
                "support, sales, operations, or another function without substantial "
                "software implementation responsibility."
            ),
        },
    ),
    "job.work.web_data_acquisition": Feature(
        key="job.work.web_data_acquisition",
        scope="job",
        version=1,
        instructions=(
            "Does this job involve acquiring data from external web sources? Count "
            "web scraping, crawling, browser automation, public-web extraction, "
            "third-party API ingestion, or building systems that collect external "
            "online data. Use explicit responsibilities in the posting. Do not infer "
            "this from general backend development, databases, or data analysis."
        ),
        criteria={
            "true": (
                "The posting explicitly describes scraping, crawling, browser "
                "automation, web extraction, or ingestion from external web sources "
                "or APIs as part of the job."
            ),
            "false": (
                "The posting does not describe external web-data acquisition, or the "
                "evidence is limited to general software, backend, database, or data "
                "analysis work."
            ),
        },
    ),
    "job.work.data_filtering_ranking": Feature(
        key="job.work.data_filtering_ranking",
        scope="job",
        version=1,
        instructions=(
            "Does this job involve turning data into filters, classifications, "
            "scores, rankings, search results, recommendations, or enriched records? "
            "Count explicit work on semantic filtering, ranking, relevance, search, "
            "recommendation, or data classification. Do not count ordinary database "
            "queries or generic analytics unless they are a central responsibility."
        ),
        criteria={
            "true": (
                "The posting explicitly describes building or maintaining systems for "
                "filtering, classification, scoring, ranking, search, recommendations, "
                "relevance, or data enrichment."
            ),
            "false": (
                "The posting contains only ordinary CRUD queries, reporting, generic "
                "analytics, or insufficient evidence of this kind of work."
            ),
        },
    ),
    "job.work.product_ui_ux": Feature(
        key="job.work.product_ui_ux",
        scope="job",
        version=1,
        instructions=(
            "Does this job involve materially shaping and building the user experience "
            "of an interactive product? Count responsibility for interaction design, "
            "usability, user flows, information architecture, accessibility, design "
            "systems, prototyping, user feedback, or close collaboration with design "
            "to improve a product experience. Do not count merely implementing fully "
            "specified screens or generic statements that the team builds a product."
        ),
        criteria={
            "true": (
                "The posting gives the role meaningful responsibility for the quality, "
                "usability, interaction, or design of a user-facing product experience."
            ),
            "false": (
                "The role has no meaningful UX responsibility, only implements "
                "predefined interfaces, or lacks enough evidence of shaping user experience."
            ),
        },
    ),
    "job.work.geospatial_maps": Feature(
        key="job.work.geospatial_maps",
        scope="job",
        version=1,
        instructions=(
            "Does this job involve maps, geospatial data, location-based products, "
            "GIS, routing, spatial analysis, or geographic visualization? Count this "
            "only when it is an explicit part of the job or product, not merely because "
            "the company operates in multiple locations."
        ),
        criteria={
            "true": (
                "The posting explicitly mentions maps, mapping interfaces, geospatial "
                "data, GIS, routing, spatial computation, or geographic visualization."
            ),
            "false": (
                "The posting does not describe geospatial or mapping work, or only "
                "mentions locations, offices, or geographic markets."
            ),
        },
    ),
    "job.work.applied_ai": Feature(
        key="job.work.applied_ai",
        scope="job",
        version=1,
        instructions=(
            "Does this job involve integrating AI or machine-learning models into "
            "real products or operational workflows? Count building AI-powered product "
            "features, model-backed decisions, LLM applications, or production inference "
            "systems. Do not count generic AI interest, model training unrelated to a "
            "product, or research descriptions without an application context."
        ),
        criteria={
            "true": (
                "The posting explicitly describes shipping, integrating, or operating "
                "AI or machine-learning capabilities as part of a product or workflow."
            ),
            "false": (
                "The posting only mentions AI generally, focuses on unrelated research "
                "or model training, or does not provide enough evidence of applied AI work."
            ),
        },
    ),
    "job.work.product_ownership": Feature(
        key="job.work.product_ownership",
        scope="job",
        version=1,
        instructions=(
            "Does this job give the person meaningful ownership of product problems "
            "and outcomes beyond completing assigned implementation tasks? Count "
            "customer or user discovery, defining problems, prioritization, zero-to-one "
            "work, navigating ambiguity, end-to-end ownership, creating new initiatives, "
            "or making product and technical tradeoffs. Require concrete responsibilities; "
            "do not count entrepreneurial, intrapreneurial, ownership, or self-starter "
            "language by itself."
        ),
        criteria={
            "true": (
                "The posting assigns concrete responsibility for discovering or defining "
                "product problems, creating a new initiative or product, prioritizing "
                "work, making tradeoffs, and owning outcomes end to end."
            ),
            "false": (
                "The role primarily executes predefined work, or the posting contains "
                "only generic ownership or entrepreneurial language without concrete "
                "product responsibilities."
            ),
        },
    ),
    "job.work.growth_experimentation": Feature(
        key="job.work.growth_experimentation",
        scope="job",
        version=1,
        instructions=(
            "Does this job involve measurable product-growth experimentation? Count "
            "work on acquisition, activation, conversion, engagement, retention, "
            "monetization, funnels, lifecycle optimization, growth loops, A/B tests, "
            "or experiments intended to change user or business behavior. Do not count "
            "generic statements that the company, team, or market is growing."
        ),
        criteria={
            "true": (
                "The posting explicitly describes owning or building experiments, "
                "metrics, systems, or product changes for measurable acquisition, "
                "activation, conversion, engagement, retention, or monetization outcomes."
            ),
            "false": (
                "The posting only mentions company growth, marketing generally, ordinary "
                "analytics, or lacks concrete product-growth responsibilities."
            ),
        },
    ),
    "job.work.rapid_prototyping": Feature(
        key="job.work.rapid_prototyping",
        scope="job",
        version=1,
        instructions=(
            "Does this job involve rapidly turning ideas, user needs, or hypotheses "
            "into working software prototypes, demos, proofs of concept, MVPs, or "
            "experiments? Count explicit expectations to prototype quickly, iterate "
            "with users, validate ideas, or move from concept to working software in "
            "short feedback cycles. Include code-based and product/UI prototyping. Do "
            "not count static mockups, unrelated hardware or manufacturing prototypes, "
            "or generic statements about moving fast."
        ),
        criteria={
            "true": (
                "The posting explicitly describes rapid software prototyping, fast "
                "iteration, proof-of-concept or MVP development, experimentation, or "
                "quickly testing working ideas with users."
            ),
            "false": (
                "The posting does not describe rapid iterative software prototyping, "
                "or only mentions static designs, unrelated physical prototypes, or "
                "generic speed."
            ),
        },
    ),
    "job.work.legacy_code_modernization": Feature(
        key="job.work.legacy_code_modernization",
        scope="job",
        version=1,
        instructions=(
            "Does this job involve a meaningful responsibility for migrating, modernizing, "
            "refactoring, or maintaining legacy software systems or codebases? Count "
            "explicit work involving legacy code, monolith modernization, technical debt, "
            "framework or language upgrades, system rewrites, platform migrations, "
            "deprecating old systems, backward compatibility, or sustaining older "
            "production systems. Require evidence that legacy or existing-system work is "
            "a meaningful part of the role. Do not infer it merely because the job "
            "maintains production software or works at an established company."
        ),
        criteria={
            "true": (
                "The posting explicitly describes migrating, modernizing, refactoring, "
                "rewriting, upgrading, or maintaining legacy codebases or older systems "
                "as a meaningful responsibility."
            ),
            "false": (
                "The posting does not mention legacy or existing-system modernization, "
                "or only describes ordinary software maintenance, production support, "
                "bug fixing, or generic refactoring."
            ),
        },
    ),
    "job.requirement.korean_language": Feature(
        key="job.requirement.korean_language",
        scope="job",
        version=2,
        instructions=(
            "Does this job explicitly require, prefer, or identify "
            "Korean-language proficiency as useful for the role? Count native, fluent, "
            "professional, business, conversational, written, or spoken Korean when "
            "presented as a qualification, preference, or job responsibility. Do not "
            "count Korean nationality, a Korean person's name, Korean-language equal-"
            "opportunity text, or merely working with the Korean market without mentioning "
            "language proficiency. Do not count a role whose primary work location is "
            "South Korea as evidence for this San Francisco-targeted filter."
        ),
        criteria={
            "true": (
                "The posting explicitly requires or prefers Korean-language proficiency, "
                "or identifies Korean fluency as useful for performing the role."
            ),
            "false": (
                "The posting does not mention Korean-language ability, mentions Korea "
                "only through nationality or geography, or the role is based in South "
                "Korea without a relevant San Francisco-targeted requirement."
            ),
        },
    ),
    "job.travel.south_korea": Feature(
        key="job.travel.south_korea",
        scope="job",
        version=2,
        instructions=(
            "Does this job explicitly require or expect recurring work "
            "travel to South Korea, including Seoul? Count regular, frequent, periodic, "
            "or clearly expected visits to South Korean offices, customers, partners, "
            "or teams. Do not count a job that is based in South Korea, generic "
            "international or APAC travel, a one-time possible trip, or a company merely "
            "having an office in South Korea."
        ),
        criteria={
            "true": (
                "The posting explicitly indicates that the employee will make recurring "
                "or expected work trips to South Korea."
            ),
            "false": (
                "The role is located in South Korea, mentions only generic travel, or "
                "does not provide evidence of recurring South Korea travel."
            ),
        },
    ),
    "job.work.south_korea_business": Feature(
        key="job.work.south_korea_business",
        scope="job",
        version=2,
        instructions=(
            "Does this job have substantial ongoing responsibility for "
            "South Korean customers, users, partners, markets, operations, products, "
            "or teams? Count roles that explicitly own or regularly support Korean "
            "business activity. Do not count a generic APAC responsibility, an isolated "
            "reference to Korea, or a company presence in South Korea unrelated to this "
            "role. Do not count a role whose primary work location is South Korea."
        ),
        criteria={
            "true": (
                "The role explicitly performs meaningful recurring work with South Korean "
                "customers, partners, markets, operations, products, offices, or teams."
            ),
            "false": (
                "The posting provides no role-specific South Korea responsibility, only "
                "mentions APAC, Asia, international work, or Korea incidentally, or the "
                "role is based in South Korea."
            ),
        },
    ),
    "job.culture.extreme_work_expectations": Feature(
        key="job.culture.extreme_work_expectations",
        scope="job",
        version=1,
        instructions=(
            "Does this job posting explicitly signal or glorify extreme work "
            "intensity, unusually long hours, weak work-life boundaries, or an "
            "expectation of always-on or superhuman performance? Count explicit "
            "references to hustle culture, work hard/play hard as a work expectation, "
            "996 schedules, regular nights or weekends, no-work-life-balance language, "
            "relentless availability, do-whatever-it-takes language, or 10x engineer "
            "expectations. Judge the job posting itself. Do not infer culture from the "
            "company, industry, startup status, fast pace, high ownership, ambition, "
            "or ordinary on-call duties."
        ),
        criteria={
            "true": (
                "The posting contains direct language suggesting or celebrating "
                "extreme hours, persistent availability, sacrifice of personal time, "
                "or superhuman performance expectations."
            ),
            "false": (
                "The posting contains no such signal, or only uses ordinary language "
                "such as fast-paced, high ownership, ambitious, startup, or occasional "
                "on-call."
            ),
        },
    ),
    "job.mentions.varied_backgrounds": Feature(
        key="job.mentions.varied_backgrounds",
        scope="job",
        version=2,
        instructions=(
            "Does this job posting explicitly encourage applications from candidates "
            "with varied, underrepresented, unconventional, or non-traditional "
            "backgrounds, including candidates who may not meet every listed "
            "qualification? Evaluate only language directed toward potential applicants. "
            "Do not count generic equal-opportunity boilerplate, demographic lists, "
            "accommodation notices, or general statements that the company values diversity."
        ),
        criteria={
            "true": (
                "The posting directly encourages such candidates to apply. Qualifying "
                "examples include welcoming non-traditional backgrounds, career changers, "
                "self-taught candidates, transferable experience, underrepresented groups, "
                "or people who do not satisfy every requirement."
            ),
            "false": (
                "The posting contains no applicant-directed encouragement, or contains "
                "only an equal-opportunity statement, protected-class list, accommodation "
                "notice, demographic disclosure, or a generic statement about diversity."
            ),
        },
    ),
    "job.workflow.typesafe_or_jev_like": Feature(
        key="job.workflow.typesafe_or_jev_like",
        scope="job",
        version=2,
        instructions=(
            "Does this job posting explicitly mention TypeSafe, Jev, or System One, "
            "or explicitly describe a closely equivalent workflow in which an AI "
            "model produces narrow typed judgments, structured decisions, "
            "classifications, scores, or probabilities that application code "
            "consumes for filtering, ranking, routing, extraction, or verification? "
            "Require concrete workflow evidence from the posting. Do not infer a "
            "match merely because the role uses LLMs, agents, prompts, RAG, "
            "structured outputs, or AI generally."
        ),
        criteria={
            "true": (
                "The posting names TypeSafe, Jev, or System One, or clearly describes "
                "using narrow model judgments as structured program inputs that code "
                "combines into application behavior."
            ),
            "false": (
                "The posting contains only general AI, LLM, agent, RAG, prompt-"
                "engineering, model-training, evaluation, or structured-output work "
                "without describing model judgments used as composable programming "
                "primitives."
            ),
        },
    ),
    "job.salary.under_200k": Feature(
        key="job.salary.under_200k",
        scope="job",
        version=1,
        instructions=(
            "Does the job posting state an annual base-salary maximum below "
            "$200,000? Use only explicit compensation text. Do not infer salary "
            "from the role, seniority, company, equity, or market rates."
        ),
        criteria={
            "true": "The explicit annual base-salary maximum is below $200,000.",
            "false": "The maximum is $200,000 or more, or salary is missing, non-annual, or ambiguous.",
        },
    ),
    "job.salary.200k_300k": Feature(
        key="job.salary.200k_300k",
        scope="job",
        version=1,
        instructions=(
            "Does the job posting state an annual base-salary range whose "
            "maximum is at least $200,000 and below $300,000? Use only explicit "
            "compensation text. Do not infer salary."
        ),
        criteria={
            "true": "The explicit annual base-salary maximum is $200,000-$299,999.",
            "false": "The maximum is outside that range, or salary is missing, non-annual, or ambiguous.",
        },
    ),
    "job.salary.300k_400k": Feature(
        key="job.salary.300k_400k",
        scope="job",
        version=1,
        instructions=(
            "Does the job posting state an annual base-salary range whose "
            "maximum is at least $300,000 and no more than $400,000? Use only "
            "explicit compensation text. Do not infer salary."
        ),
        criteria={
            "true": "The explicit annual base-salary maximum is $300,000-$400,000.",
            "false": "The maximum is outside that range, or salary is missing, non-annual, or ambiguous.",
        },
    ),
    "job.salary.over_400k": Feature(
        key="job.salary.over_400k",
        scope="job",
        version=1,
        instructions=(
            "Does the job posting state an annual base-salary maximum above "
            "$400,000? Use only explicit compensation text. Do not infer salary "
            "from the role, seniority, company, equity, or market rates."
        ),
        criteria={
            "true": "The explicit annual base-salary maximum is above $400,000.",
            "false": "The maximum is $400,000 or less, or salary is missing, non-annual, or ambiguous.",
        },
    ),
    "company.origin.south_korea": Feature(
        key="company.origin.south_korea",
        scope="company",
        version=1,
        instructions=(
            "Is this company explicitly founded, headquartered, or primarily based in "
            "South Korea while also having a verified San Francisco presence? Require "
            "direct company evidence from the company record, research, locations, or "
            "explicit company descriptions in the bounded job evidence. Do not count a "
            "company merely because it has Korean customers, a Korean office, Korean "
            "employees, or operations somewhere in South Korea."
        ),
        criteria={
            "true": (
                "Direct evidence identifies the company as founded, headquartered, or "
                "primarily based in South Korea and the company has a verified San Francisco "
                "presence."
            ),
            "false": (
                "The company is not South Korean-founded or based there, lacks a verified "
                "San Francisco presence, or the evidence shows only customers, employees, "
                "partnerships, or an office without company origin evidence."
            ),
        },
    ),
}


# This is the only section that determines the current search.  Probabilities
# remain cached independently, so changing a threshold does not call Jev again.
ACTIVE_FILTERS: dict[str, float] = {
    "job.location.san_francisco": 0.80,
    # Uncomment or add filters as the exploration develops:
    # Workplace filters are independent and can be enabled together:
    # "job.workplace.onsite": 0.80,
    # "job.workplace.hybrid": 0.80,
    # "job.workplace.remote": 0.80,
    # Project/work signals; these are reusable inputs for later group scores:
    # "job.role.frontend_engineering": 0.80,
    # "job.role.product_ux_design": 0.80,
    # "job.role.technical": 0.80,
    # "job.role.software_engineering": 0.80,
    # "job.work.web_data_acquisition": 0.80,
    # "job.work.data_filtering_ranking": 0.80,
    # "job.work.product_ui_ux": 0.80,
    # "job.work.research_ux_ui": 0.80,
    # "job.domain.education": 0.80,
    # "job.work.geospatial_maps": 0.80,
    # "job.work.applied_ai": 0.80,
    # "job.work.product_ownership": 0.80,
    # "job.work.growth_experimentation": 0.80,
    # "job.work.rapid_prototyping": 0.80,
    # "job.work.legacy_code_modernization": 0.80,
    # South Korea signals; the SF location filter remains independent:
    # "job.requirement.korean_language": 0.80,
    # "job.travel.south_korea": 0.80,
    # "job.work.south_korea_business": 0.80,
    # "company.origin.south_korea": 0.80,
    # Negative culture signal; high probability means the job should be excluded:
    # "job.culture.extreme_work_expectations": 0.80,
    # Select one salary bucket at a time, then run with --all:
    # "job.salary.under_200k": 0.80,
    # "job.salary.200k_300k": 0.80,
    # "job.salary.300k_400k": 0.80,
    # "job.salary.over_400k": 0.80,
}


def parse_json_value(value: str | None) -> Any:
    return json.loads(value) if value else None


def trim_text(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if len(text) <= limit:
        return text
    first = max(1, int(limit * 0.75))
    last = max(1, limit - first)
    return f"{text[:first]}\n...[truncated]...\n{text[-last:]}"


def row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def load_api_key() -> str:
    if os.environ.get("TYPESAFE_API_KEY"):
        return os.environ["TYPESAFE_API_KEY"]

    env_path = PROJECT_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("TYPESAFE_API_KEY="):
                return line.split("=", 1)[1].strip().strip("\"'")

    raise RuntimeError("TYPESAFE_API_KEY is missing from the environment and .env")


def select_jobs(
    connection: sqlite3.Connection,
    job_id: str | None,
    limit: int | None,
    random_order: bool,
) -> list[dict[str, Any]]:
    order_by = "RANDOM()" if random_order else "j.job_id"
    if job_id:
        rows = connection.execute(
            """
            SELECT j.*, cs.company_id
            FROM jobs AS j
            LEFT JOIN career_sources AS cs ON cs.source_id = j.source_id
            WHERE j.job_id = ?
            """,
            (job_id,),
        ).fetchall()
    else:
        query = f"""
            SELECT j.*, cs.company_id
            FROM jobs AS j
            LEFT JOIN career_sources AS cs ON cs.source_id = j.source_id
            WHERE j.status = 'active'
            ORDER BY {order_by}
        """
        parameters: tuple[int, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            parameters = (limit,)
        rows = connection.execute(query, parameters).fetchall()
    return [row_dict(row) for row in rows]


def in_clause(values: list[str]) -> str:
    return ", ".join("?" for _ in values)


def select_by_company_ids(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    company_ids: list[str],
    extra_where: str = "",
) -> list[dict[str, Any]]:
    if not company_ids:
        return []
    query = f"SELECT * FROM {table} WHERE {column} IN ({in_clause(company_ids)})"
    if extra_where:
        query += f" AND {extra_where}"
    return [row_dict(row) for row in connection.execute(query, company_ids)]


def build_job_state(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": job.get("title"),
        "description": trim_text(job.get("description_text"), MAX_JOB_DESCRIPTION_CHARS),
        "workplace_text": job.get("workplace_text"),
        "employment_text": job.get("employment_text"),
        "compensation_text": job.get("compensation_text"),
        "posting_locations": parse_json_value(job.get("posting_locations_json")),
        "job_details": trim_text(
            parse_json_value(job.get("job_details_json")), MAX_JOB_DETAILS_CHARS
        ),
    }


def build_company_states(
    connection: sqlite3.Connection,
    company_ids: list[str],
) -> dict[str, dict[str, Any]]:
    companies = select_by_company_ids(connection, "companies", "company_id", company_ids)
    locations = select_by_company_ids(
        connection,
        "company_locations",
        "company_id",
        company_ids,
        "is_current = 1",
    )
    evidence = select_by_company_ids(
        connection,
        "company_location_evidence",
        "company_id",
        company_ids,
    )
    research = select_by_company_ids(
        connection,
        "company_research_records",
        "company_id",
        company_ids,
    )
    sources = select_by_company_ids(
        connection,
        "career_sources",
        "company_id",
        company_ids,
        "is_current = 1",
    )

    job_rows = [
        row_dict(row)
        for row in connection.execute(
            f"""
            SELECT j.*, cs.company_id
            FROM jobs AS j
            JOIN career_sources AS cs ON cs.source_id = j.source_id
            WHERE j.status = 'active'
              AND cs.company_id IN ({in_clause(company_ids)})
            ORDER BY j.job_id
            """,
            company_ids,
        )
    ]

    by_company: dict[str, dict[str, Any]] = {
        company_id: {
            "company": None,
            "locations": [],
            "location_evidence": [],
            "research_records": [],
            "career_sources": [],
            "job_evidence": [],
        }
        for company_id in company_ids
    }

    for company in companies:
        company_id = company["company_id"]
        by_company[company_id]["company"] = {
            "company_id": company_id,
            "company_name": company.get("company_name"),
            "website_url": company.get("website_url"),
            "sf_validation_status": company.get("sf_validation_status"),
        }

    for location in locations:
        by_company[location["company_id"]]["locations"].append(
            {
                key: location.get(key)
                for key in (
                    "street",
                    "city",
                    "region",
                    "postal_code",
                    "country",
                    "validation_status",
                    "is_physical",
                    "is_in_san_francisco",
                )
            }
        )

    for item in evidence:
        by_company[item["company_id"]]["location_evidence"].append(
            {
                "evidence_type": item.get("evidence_type"),
                "label": item.get("label"),
                "finding": trim_text(item.get("finding"), 2_000),
                "url": item.get("url"),
            }
        )

    for item in research:
        company_id = item.get("company_id")
        if company_id not in by_company:
            continue
        by_company[company_id]["research_records"].append(
            {
                "source_kind": item.get("source_kind"),
                "operation": item.get("operation"),
                "outcome": trim_text(item.get("outcome"), 2_000),
                "reason_code": item.get("reason_code"),
                "payload": trim_text(parse_json_value(item.get("payload_json")), 4_000),
            }
        )

    for source in sources:
        by_company[source["company_id"]]["career_sources"].append(
            {
                "provider": source.get("provider"),
                "careers_url": source.get("careers_url"),
                "listings_url": source.get("listings_url"),
                "scope": source.get("scope"),
            }
        )

    for job in job_rows:
        company_id = job["company_id"]
        if len(by_company[company_id]["job_evidence"]) >= MAX_COMPANY_JOB_EVIDENCE:
            continue
        by_company[company_id]["job_evidence"].append(
            {
                "title": job.get("title"),
                "posting_locations": parse_json_value(job.get("posting_locations_json")),
                "workplace_text": job.get("workplace_text"),
                "description": trim_text(
                    job.get("description_text"), MAX_COMPANY_JOB_DESCRIPTION_CHARS
                ),
            }
        )

    return by_company


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def initialize_cache(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS feature_results (
            scope TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            feature_key TEXT NOT NULL,
            feature_version INTEGER NOT NULL,
            input_hash TEXT NOT NULL,
            model TEXT NOT NULL,
            probability REAL NOT NULL,
            answer_json TEXT NOT NULL,
            evaluated_at TEXT NOT NULL,
            PRIMARY KEY (scope, entity_id, feature_key)
        )
        """
    )
    connection.commit()


def cached_result(
    connection: sqlite3.Connection,
    feature: Feature,
    entity_id: str,
    input_hash: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT *
        FROM feature_results
        WHERE scope = ? AND entity_id = ? AND feature_key = ?
        """,
        (feature.scope, entity_id, feature.key),
    ).fetchone()
    if row is None:
        return None
    if row["feature_version"] != feature.version or row["input_hash"] != input_hash:
        return None
    return row_dict(row)


def save_result(
    connection: sqlite3.Connection,
    feature: Feature,
    entity_id: str,
    input_hash: str,
    model: str,
    answer: dict[str, Any],
) -> dict[str, Any]:
    probability = float(answer["noul"])
    evaluated_at = datetime.now(timezone.utc).isoformat()
    connection.execute(
        """
        INSERT INTO feature_results (
            scope, entity_id, feature_key, feature_version, input_hash,
            model, probability, answer_json, evaluated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(scope, entity_id, feature_key) DO UPDATE SET
            feature_version = excluded.feature_version,
            input_hash = excluded.input_hash,
            model = excluded.model,
            probability = excluded.probability,
            answer_json = excluded.answer_json,
            evaluated_at = excluded.evaluated_at
        """,
        (
            feature.scope,
            entity_id,
            feature.key,
            feature.version,
            input_hash,
            model,
            probability,
            json.dumps(answer, ensure_ascii=False),
            evaluated_at,
        ),
    )
    connection.commit()
    return {
        "scope": feature.scope,
        "entity_id": entity_id,
        "feature_key": feature.key,
        "feature_version": feature.version,
        "input_hash": input_hash,
        "model": model,
        "probability": probability,
        "answer_json": json.dumps(answer, ensure_ascii=False),
        "evaluated_at": evaluated_at,
    }


def ask_jev(
    api_key: str,
    state: dict[str, Any],
    features: list[Feature],
) -> tuple[str, dict[str, dict[str, Any]]]:
    question_ids = {f"q{index}": feature for index, feature in enumerate(features)}
    request_body = {
        "state": state,
        "model": MODEL,
        "questions": {
            question_id: {
                "type": "noul",
                "instructions": feature.instructions,
                "criteria": feature.criteria,
            }
            for question_id, feature in question_ids.items()
        },
    }
    request = Request(
        TYPESAFE_URL,
        data=json.dumps(request_body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    for attempt in range(4):
        try:
            with urlopen(request, timeout=90) as response:
                result = json.load(response)
            if not isinstance(result, dict):
                raise RuntimeError("Jev returned a non-object response")
            answers = result.get("answers", {})
            if not isinstance(answers, dict):
                raise RuntimeError("Jev returned invalid answers")
            mapped: dict[str, dict[str, Any]] = {}
            for question_id, feature in question_ids.items():
                answer = answers.get(question_id)
                if not isinstance(answer, dict) or "noul" not in answer:
                    raise RuntimeError(f"Jev response omitted answer for {feature.key}")
                probability = float(answer["noul"])
                if not 0 <= probability <= 1:
                    raise RuntimeError(
                        f"Jev returned an invalid probability for {feature.key}"
                    )
                mapped[feature.key] = answer
            return result.get("model", MODEL), mapped
        except HTTPError as error:
            if (
                error.code not in (408, 429, 500, 502, 503, 504, 529)
                or attempt == 3
            ):
                raise
            time.sleep(2**attempt)
        except (URLError, TimeoutError, json.JSONDecodeError):
            if attempt == 3:
                raise
            time.sleep(2**attempt)

    raise RuntimeError("Jev request did not return an answer")


def evaluate_scope(
    api_key: str | None,
    cache: sqlite3.Connection,
    contexts: dict[str, dict[str, Any]],
    features: list[Feature],
    dry_run: bool,
) -> tuple[
    dict[tuple[str, str], dict[str, Any]],
    dict[str, int],
    list[dict[str, Any]],
]:
    results: dict[tuple[str, str], dict[str, Any]] = {}
    stats = {
        "total_entities": len(contexts),
        "processed_entities": 0,
        "cache_hits": 0,
        "evaluated": 0,
        "missing_in_dry_run": 0,
        "failed_entities": 0,
    }
    failures: list[dict[str, Any]] = []
    if not contexts or not features:
        return results, stats, failures

    scope = features[0].scope

    def show_progress() -> None:
        print(
            f"[{scope}] {stats['processed_entities']}/{stats['total_entities']} entities | "
            f"{stats['cache_hits']} cached | {stats['evaluated']} evaluated | "
            f"{stats['failed_entities']} failed",
            file=sys.stderr,
            flush=True,
        )

    for entity_id, state in contexts.items():
        input_hash = stable_hash(state)
        missing: list[Feature] = []
        for feature in features:
            cached = cached_result(cache, feature, entity_id, input_hash)
            if cached is None:
                missing.append(feature)
            else:
                results[(entity_id, feature.key)] = cached
                stats["cache_hits"] += 1

        if missing:
            if dry_run:
                stats["missing_in_dry_run"] += len(missing)
            else:
                if api_key is None:
                    raise RuntimeError("An API key is required for non-dry runs")
                try:
                    model, answers = ask_jev(api_key, state, missing)
                    for feature in missing:
                        saved = save_result(
                            cache,
                            feature,
                            entity_id,
                            input_hash,
                            model,
                            answers[feature.key],
                        )
                        results[(entity_id, feature.key)] = saved
                        stats["evaluated"] += 1
                except HTTPError as error:
                    if 400 <= error.code < 500 and error.code not in (408, 429):
                        raise
                    stats["failed_entities"] += 1
                    failures.append(
                        {
                            "scope": scope,
                            "entity_id": entity_id,
                            "features": [feature.key for feature in missing],
                            "error_type": type(error).__name__,
                            "message": str(error),
                        }
                    )
                except (
                    URLError,
                    TimeoutError,
                    json.JSONDecodeError,
                    RuntimeError,
                    TypeError,
                    ValueError,
                    KeyError,
                ) as error:
                    stats["failed_entities"] += 1
                    failures.append(
                        {
                            "scope": scope,
                            "entity_id": entity_id,
                            "features": [feature.key for feature in missing],
                            "error_type": type(error).__name__,
                            "message": str(error),
                        }
                    )

        stats["processed_entities"] += 1
        if (
            stats["processed_entities"] % PROGRESS_EVERY == 0
            or stats["processed_entities"] == stats["total_entities"]
        ):
            show_progress()

    return results, stats, failures


def parse_filters(values: list[str] | None) -> dict[str, float]:
    if not values:
        return dict(ACTIVE_FILTERS)

    filters: dict[str, float] = {}
    for value in values:
        try:
            feature_key, threshold_text = value.split("=", 1)
            threshold = float(threshold_text)
        except ValueError as error:
            raise SystemExit(f"Invalid --filter {value!r}; use FEATURE=THRESHOLD") from error
        if feature_key not in FEATURES:
            raise SystemExit(f"Unknown feature {feature_key!r}; use --list-features")
        if not 0 <= threshold <= 1:
            raise SystemExit("Filter thresholds must be between 0 and 1")
        filters[feature_key] = threshold
    return filters


def filter_jobs(
    jobs: list[dict[str, Any]],
    filters: dict[str, float],
    results: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for job in jobs:
        job_id = job["job_id"]
        company_id = job.get("company_id")
        feature_values: dict[str, float | None] = {}
        passed = True

        for feature_key, threshold in filters.items():
            feature = FEATURES[feature_key]
            entity_id = job_id if feature.scope == "job" else company_id
            result = results.get((entity_id, feature_key)) if entity_id else None
            probability = float(result["probability"]) if result else None
            feature_values[feature_key] = probability
            if probability is None or probability < threshold:
                passed = False

        if passed:
            matches.append(
                {
                    "job_id": job_id,
                    "company_id": company_id,
                    "title": job.get("title"),
                    "url": job.get("canonical_url"),
                    "posting_locations": parse_json_value(job.get("posting_locations_json")),
                    "features": feature_values,
                }
            )
    return matches


def list_features() -> None:
    for feature in FEATURES.values():
        print(f"{feature.key}\t(scope={feature.scope}, version={feature.version})")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate reusable job/company features with Jev and filter jobs"
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--limit", type=int, default=100, help="Active jobs to inspect (default: 100)"
    )
    selection.add_argument(
        "--all", action="store_true", help="Inspect every active job"
    )
    selection.add_argument("--job-id", help="Inspect one exact job")
    parser.add_argument("--random", action="store_true", help="Choose a random sample")
    parser.add_argument(
        "--filter",
        action="append",
        metavar="FEATURE=THRESHOLD",
        help="Override active filters; may be supplied more than once",
    )
    parser.add_argument("--cache", type=Path, default=CACHE_PATH)
    parser.add_argument(
        "--output", type=Path, help="Also write the JSON report to this path"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show missing evaluations without calling Jev",
    )
    parser.add_argument(
        "--evaluate-all",
        action="store_true",
        help=(
            "Evaluate and cache all non-deferred features; active filters still "
            "control the report"
        ),
    )
    parser.add_argument("--list-features", action="store_true")
    args = parser.parse_args()

    if args.list_features:
        list_features()
        return
    if not args.all and args.limit < 1:
        raise SystemExit("--limit must be at least 1")
    if args.all and args.random:
        parser.error("--all cannot be combined with --random")
    if args.job_id and args.random:
        parser.error("--job-id cannot be combined with --random")

    filters = parse_filters(args.filter)
    if args.evaluate_all:
        deferred_filters = sorted(set(filters) & DEFERRED_FEATURE_KEYS)
        if deferred_filters:
            parser.error(
                "--evaluate-all excludes deferred features: "
                + ", ".join(deferred_filters)
            )
        evaluation_features = [
            feature
            for key, feature in FEATURES.items()
            if key not in DEFERRED_FEATURE_KEYS
        ]
    else:
        evaluation_features = [FEATURES[key] for key in filters]
    company_features = [
        feature for feature in evaluation_features if feature.scope == "company"
    ]
    job_features = [
        feature for feature in evaluation_features if feature.scope == "job"
    ]

    source_uri = f"file:{DATABASE_PATH}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as source:
        source.row_factory = sqlite3.Row
        limit = None if args.all else args.limit
        jobs = select_jobs(source, args.job_id, limit, args.random)
        if not jobs:
            raise SystemExit("No matching jobs found")
        company_ids = sorted({job["company_id"] for job in jobs if job.get("company_id")})
        company_states = (
            build_company_states(source, company_ids) if company_features else {}
        )
        job_states = {job["job_id"]: build_job_state(job) for job in jobs}

    cache_path = args.cache.expanduser()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(cache_path) as cache:
        cache.row_factory = sqlite3.Row
        initialize_cache(cache)
        api_key = None if args.dry_run else load_api_key()

        company_results, company_stats, company_failures = evaluate_scope(
            api_key, cache, company_states, company_features, args.dry_run
        )
        job_results, job_stats, job_failures = evaluate_scope(
            api_key, cache, job_states, job_features, args.dry_run
        )

    all_results = {**company_results, **job_results}
    matches = filter_jobs(jobs, filters, all_results)
    failures = company_failures + job_failures
    remaining = (
        company_stats["missing_in_dry_run"]
        + job_stats["missing_in_dry_run"]
        + sum(len(failure["features"]) for failure in failures)
    )
    report = {
        "database": str(DATABASE_PATH),
        "cache": str(cache_path),
        "selected_jobs": len(jobs),
        "selected_companies": len(company_ids),
        "filters": filters,
        "evaluation_mode": "all_non_deferred" if args.evaluate_all else "active_filters",
        "evaluated_features": [feature.key for feature in evaluation_features],
        "deferred_features": sorted(DEFERRED_FEATURE_KEYS),
        "company_pass": company_stats,
        "job_pass": job_stats,
        "complete": remaining == 0,
        "remaining": remaining,
        "failures": failures,
        "matches": len(matches),
        "jobs": matches,
    }
    output = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        output_path = args.output.expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output + "\n")
        print(
            json.dumps(
                {
                    "output": str(output_path),
                    "selected_jobs": len(jobs),
                    "filters": filters,
                    "evaluation_mode": (
                        "all_non_deferred" if args.evaluate_all else "active_filters"
                    ),
                    "evaluated_feature_count": len(evaluation_features),
                    "company_pass": company_stats,
                    "job_pass": job_stats,
                    "complete": remaining == 0,
                    "remaining": remaining,
                    "failure_count": len(failures),
                    "matches": len(matches),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(output)

    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
