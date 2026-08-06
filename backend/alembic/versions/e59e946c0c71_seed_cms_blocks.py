"""seed cms blocks

Revision ID: e59e946c0c71
Revises: 7b47c9726835
Create Date: 2026-08-06 19:57:06.088947

"""
import uuid
from datetime import UTC, datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = 'e59e946c0c71'
down_revision: Union[str, None] = '7b47c9726835'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CMS_BLOCKS_TABLE = sa.table(
    "cms_blocks",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("key", sa.String),
    sa.column("page", sa.String),
    sa.column("section", sa.String),
    sa.column("label", sa.String),
    sa.column("value", sa.Text),
    sa.column("display_order", sa.Integer),
    sa.column("is_system_block", sa.Boolean),
    sa.column("is_active", sa.Boolean),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)

_CC_BY_BLURB = (
    "All datasets on this portal are released under Creative Commons "
    "Attribution 4.0 (CC BY 4.0) unless otherwise noted on the dataset's "
    "own page. You are free to share and adapt the material for any "
    "purpose, provided you give appropriate credit to the Bangladesh "
    "Oceanographic Data Portal and the original data provider, link to "
    "the license, and indicate if changes were made."
)

_PRIVACY_BLURB = (
    "<p>We collect only the information necessary to operate this "
    "portal: your name, email, and institution when you register, and "
    "basic request/access logs when you request or download a dataset. "
    "We do not sell or share your personal data with third parties "
    "except as required to fulfil a dataset access request you initiate.</p>"
    "<p>You may request a copy of your data or deletion of your account "
    "at any time from your dashboard's Security settings.</p>"
)

_TERMS_BLURB = (
    "<p>By using this portal you agree to use the data responsibly, "
    "attribute it as required by its license, and not attempt to "
    "circumvent the access-request process for restricted datasets. "
    "Accounts found to violate these terms may be suspended.</p>"
)

_DOWNLOAD_BLURB = (
    f"<p>{_CC_BY_BLURB}</p>"
    "<p>Some datasets require an approved access request before "
    "download — this is noted on the dataset's own page and does not "
    "change the underlying license, only the process for obtaining the "
    "files.</p>"
)

_CITATION_BLURB = (
    "<p>Please cite datasets from this portal as: <em>Bangladesh "
    "Oceanographic Data Portal. [Dataset Title]. Retrieved from "
    "[portal URL] on [access date].</em></p>"
    "<p>Where a dataset page provides its own suggested citation, use "
    "that instead.</p>"
)

_COOKIE_BLURB = (
    "<p>This portal uses only strictly necessary cookies to keep you "
    "signed in and remember your theme preference. We do not use "
    "third-party tracking or advertising cookies.</p>"
)

_DISCLAIMER_BLURB = (
    "<p>Data on this portal is provided as-is for research and "
    "educational use. While we work with data providers to ensure "
    "accuracy, the Bangladesh Oceanographic Data Portal makes no "
    "warranty as to the completeness or fitness of any dataset for a "
    "particular purpose.</p>"
)


def _block(key, page, section, label, value, order, *, system=True, active=True):
    return {
        "id": uuid.uuid4(),
        "key": key,
        "page": page,
        "section": section,
        "label": label,
        "value": value,
        "display_order": order,
        "is_system_block": system,
        "is_active": active,
        "updated_at": datetime.now(UTC),
    }


_SEED_BLOCKS = [
    # Home
    _block("home.hero.tag", "home", "hero", "Hero Tag", "🌊 Open Ocean & Environmental Data", 0),
    _block("home.hero.title", "home", "hero", "Hero Title", "Bangladesh Oceanographic Data Portal", 1),
    _block(
        "home.hero.subtitle", "home", "hero", "Hero Subtitle",
        "Explore, visualize, and request access to marine, coastal, and environmental datasets from the Bay of Bengal.",
        2,
    ),
    _block("home.stats.datasets_label", "home", "stats", "Datasets Stat Label", "Datasets", 3),
    _block("home.stats.users_label", "home", "stats", "Researchers Stat Label", "Researchers", 4),
    _block("home.stats.downloads_label", "home", "stats", "Downloads Stat Label", "Downloads", 5),
    _block("home.stats.institutions_label", "home", "stats", "Institutions Stat Label", "Institutions", 6),
    _block("home.categories.title", "home", "categories_intro", "Categories Title", "Explore by Category", 7),
    _block(
        "home.categories.subtitle", "home", "categories_intro", "Categories Subtitle",
        "Browse datasets organized by environmental domain.", 8,
    ),
    _block("home.workflow.step1_title", "home", "workflow", "Step 1 Title", "Browse the Catalog", 9),
    _block("home.workflow.step1_body", "home", "workflow", "Step 1 Body", "Search and filter datasets by category, location, or parameter.", 10),
    _block("home.workflow.step2_title", "home", "workflow", "Step 2 Title", "Request Access", 11),
    _block("home.workflow.step2_body", "home", "workflow", "Step 2 Body", "Submit a request with your research justification.", 12),
    _block("home.workflow.step3_title", "home", "workflow", "Step 3 Title", "Get Approved", 13),
    _block("home.workflow.step3_body", "home", "workflow", "Step 3 Body", "An administrator reviews and approves your request.", 14),
    _block("home.workflow.step4_title", "home", "workflow", "Step 4 Title", "Download & Analyze", 15),
    _block("home.workflow.step4_body", "home", "workflow", "Step 4 Body", "Access, visualize, and export the data you need.", 16),
    _block(
        "home.partners.intro", "home", "partners", "Partners Intro",
        "Working with research institutions across Bangladesh and the wider Bay of Bengal region.", 17,
    ),

    # About
    _block("about.hero.title", "about", "hero", "About Hero Title", "About the Portal", 0),
    _block(
        "about.hero.subtitle", "about", "hero", "About Hero Subtitle",
        "Building open access to Bangladesh's oceanographic and environmental data.", 1,
    ),
    _block("about.mission.title", "about", "mission", "Mission Title", "Our Mission", 2),
    _block(
        "about.mission.body", "about", "mission", "Mission Body",
        "To make marine, coastal, and environmental data from the Bay of Bengal freely and easily accessible to researchers, policymakers, and the public.",
        3,
    ),

    # Contact
    _block("contact.hero.title", "contact", "hero", "Contact Hero Title", "Get in Touch", 0),
    _block("contact.hero.subtitle", "contact", "hero", "Contact Hero Subtitle", "Questions about the data or your account? We're here to help.", 1),
    _block("contact.info.address", "contact", "info", "Office Address", "Dhaka, Bangladesh", 2),
    _block("contact.info.email", "contact", "info", "Contact Email", "info@bodp.org", 3),
    _block("contact.info.phone", "contact", "info", "Contact Phone", "+880 000 000000", 4),
    _block("contact.info.hours", "contact", "info", "Office Hours", "Sunday–Thursday, 9:00–17:00", 5),
    _block(
        "contact.faq.items", "contact", "faq", "FAQ Items (JSON)",
        '[{"q":"How do I request access to a dataset?","a":"Browse the catalog, open the dataset you need, and click Request Access. An administrator will review your justification."},'
        '{"q":"Is the data free to use?","a":"Yes, all datasets are released under CC BY 4.0 unless otherwise noted on the dataset page."},'
        '{"q":"How long does approval take?","a":"Most requests are reviewed within a few business days."}]',
        6,
    ),

    # Footer
    _block(
        "footer.brand.description", "footer", "brand", "Footer Brand Description",
        "Bangladesh Oceanographic Data Portal — Open access marine & environmental data for the Bay of Bengal region.",
        0,
    ),
    _block("footer.copyright", "footer", "columns", "Copyright Line", "© 2026 Bangladesh Oceanographic Data Portal · CC BY 4.0", 1),

    # Blog
    _block("blog.intro.title", "blog", "intro", "Blog Intro Title", "News & Insights", 0),
    _block("blog.intro.subtitle", "blog", "intro", "Blog Intro Subtitle", "Updates on new datasets, research, and portal features.", 1),
    _block("blog.newsletter.note", "blog", "intro", "Newsletter Note", "Subscribe for occasional updates — no spam, unsubscribe any time.", 2),

    # Legal pages — one title + one HTML body each, all rendered by the
    # same generic /legal/[slug] template.
    _block("legal-terms-conditions.title", "legal-terms-conditions", "content", "Title", "Terms & Conditions", 0),
    _block("legal-terms-conditions.body", "legal-terms-conditions", "content", "Body", _TERMS_BLURB, 1),
    _block("legal-privacy-policy.title", "legal-privacy-policy", "content", "Title", "Privacy Policy", 0),
    _block("legal-privacy-policy.body", "legal-privacy-policy", "content", "Body", _PRIVACY_BLURB, 1),
    _block("legal-download-policy.title", "legal-download-policy", "content", "Title", "Download Policy", 0),
    _block("legal-download-policy.body", "legal-download-policy", "content", "Body", _DOWNLOAD_BLURB, 1),
    _block("legal-citation-policy.title", "legal-citation-policy", "content", "Title", "Citation Policy", 0),
    _block("legal-citation-policy.body", "legal-citation-policy", "content", "Body", _CITATION_BLURB, 1),
    _block("legal-cookie-policy.title", "legal-cookie-policy", "content", "Title", "Cookie Policy", 0),
    _block("legal-cookie-policy.body", "legal-cookie-policy", "content", "Body", _COOKIE_BLURB, 1),
    _block("legal-disclaimer.title", "legal-disclaimer", "content", "Title", "Disclaimer", 0),
    _block("legal-disclaimer.body", "legal-disclaimer", "content", "Body", _DISCLAIMER_BLURB, 1),
]


def upgrade() -> None:
    bind = op.get_bind()
    existing_keys = set(bind.execute(sa.select(_CMS_BLOCKS_TABLE.c.key)).scalars().all())

    to_insert = [b for b in _SEED_BLOCKS if b["key"] not in existing_keys]
    if to_insert:
        bind.execute(_CMS_BLOCKS_TABLE.insert(), to_insert)


def downgrade() -> None:
    bind = op.get_bind()
    seed_keys = [b["key"] for b in _SEED_BLOCKS]
    bind.execute(_CMS_BLOCKS_TABLE.delete().where(_CMS_BLOCKS_TABLE.c.key.in_(seed_keys)))
