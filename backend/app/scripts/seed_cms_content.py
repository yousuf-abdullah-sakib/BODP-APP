"""Seeds real cms_blocks rows for the dashboard's Guidelines and Help
Center sections (Master Plan §3 Phase 6 task 7) — content ported from the
bodp-frontend prototype's hardcoded GuidelinesSection.tsx/HelpCenterSection.tsx,
landed as genuine rows served through GET /me/cms-blocks?page=... instead of
being hardcoded in the frontend. Two fixes made during porting:
  - The prototype's Access & Renewal panel claimed a fixed "12 months"
    grant duration; real grants are admin-set per request (5/10 days,
    1/2/6 months, 1 year, or a custom date — see GrantDuration), so the
    copy now describes that instead of a fixed period.
  - The prototype FAQ referenced an "API Access"/API key feature that
    doesn't exist in this build; that entry is dropped.

Usage:
    python -m app.scripts.seed_cms_content [--reset]

--reset deletes existing rows for these two pages first so the script is
safely re-runnable during development.
"""

import argparse

from sqlalchemy import delete

from app.core.database import SyncSessionLocal
from app.models.admin import CmsBlock

GUIDELINES_BLOCKS = [
    (
        "dashboard-guidelines-license",
        "License",
        "All datasets on BODP are published under the Creative Commons Attribution 4.0 "
        "International (CC BY 4.0) licence. You are free to share and adapt the data for "
        "any purpose, including commercial use, provided you give appropriate credit.",
    ),
    (
        "dashboard-guidelines-citation",
        "Citation Format",
        "When using BODP data in a publication, please cite as follows:\n\n"
        "Bangladesh Oceanographic Data Portal (BODP). (2024). [Dataset Title] [Data set]. "
        "Bangladesh Oceanographic Research Institute. Retrieved from https://bodp.gov.bd "
        "— Dataset ID: [BD-XXXX]",
    ),
    (
        "dashboard-guidelines-acceptable-use",
        "Acceptable Use",
        "Use data for research, education, policy analysis, and public-interest reporting.\n"
        "Attribute BODP and cite the specific dataset ID in any publication or derived product.\n"
        "Do not misrepresent the accuracy, currency, or source of the data.\n"
        "Do not redistribute restricted (non-approved) datasets to third parties.\n"
        "Report suspected data quality issues to science@bodp.gov.bd.",
    ),
    (
        "dashboard-guidelines-access-renewal",
        "Access & Renewal",
        "Access grants run for a duration set by the reviewing admin when your request is "
        "approved — commonly 5 days, 10 days, 1, 2, or 6 months, or 1 year, though a custom "
        "expiry date may be set instead. You will be notified before your access expires and "
        "may submit a new request from Browse & Search at any time to renew it.",
    ),
]

HELP_BLOCKS = [
    (
        "dashboard-help-request-access",
        "How do I request access to a dataset?",
        "Go to Browse & Search, select the dataset you need, fill in a research justification "
        "of at least 50 characters, and submit. You'll see the status under My Requests.",
    ),
    (
        "dashboard-help-approval-time",
        "How long does approval take?",
        "Most requests are reviewed within 1–2 business days. You'll receive a notification "
        "as soon as a decision is made.",
    ),
    (
        "dashboard-help-download-again",
        "Can I download a dataset more than once?",
        "Yes — approved datasets remain available for download until their access grant "
        "expires, shown on each dataset card in My Datasets.",
    ),
    (
        "dashboard-help-access-expires",
        "What happens when my access expires?",
        "You'll be notified before expiry. Submit a new request from Browse & Search to "
        "renew access.",
    ),
    (
        "dashboard-help-change-password",
        "How do I change my password?",
        "Go to Security in the sidebar, enter your current and new password, and click "
        "Update Password. This will sign you out of any other devices you're logged in on.",
    ),
]


def seed(reset: bool = False) -> None:
    with SyncSessionLocal() as db:
        if reset:
            db.execute(delete(CmsBlock).where(CmsBlock.page == "dashboard-guidelines"))
            db.execute(delete(CmsBlock).where(CmsBlock.page == "dashboard-help"))
            db.commit()
            print("Cleared existing dashboard-guidelines/dashboard-help cms_blocks rows.")

        seeded = 0
        for page, blocks in (
            ("dashboard-guidelines", GUIDELINES_BLOCKS),
            ("dashboard-help", HELP_BLOCKS),
        ):
            for key, label, value in blocks:
                existing = db.get(CmsBlock, key)
                if existing:
                    print(f"  Skipping {key} (already exists)")
                    continue
                db.add(CmsBlock(key=key, page=page, label=label, value=value))
                seeded += 1
        db.commit()
        print(f"Seeded {seeded} cms_blocks row(s).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="Clear existing rows for these pages first")
    args = parser.parse_args()
    seed(reset=args.reset)
