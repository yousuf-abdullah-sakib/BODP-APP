import nh3

# Allowlist covers everything the CMS/blog rich-content requirement calls
# for (headings, lists, tables, links, images, basic formatting) without
# ever permitting script/event-handler injection. Applied unconditionally
# to every CmsBlock.value and BlogPost.content_html write — a plain-text
# value sanitizes to itself harmlessly, so there's no need for a per-block
# "is this HTML" flag.
_ALLOWED_TAGS = {
    "p", "br", "strong", "em", "u", "s",
    "h1", "h2", "h3", "h4",
    "ul", "ol", "li",
    "a", "img",
    "table", "thead", "tbody", "tr", "th", "td",
    "blockquote", "code", "pre", "span", "div",
}
_ALLOWED_ATTRIBUTES = {
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "title", "width", "height"},
    "*": {"class"},
}


def sanitize_html(raw: str | None) -> str | None:
    """Strips script tags, inline event handlers, and anything outside the
    allowlist above — the real server-side defense the prototype's raw
    dangerouslySetInnerHTML rendering never had. Called at write time, in
    the service layer, before every commit of admin-supplied HTML."""
    if raw is None:
        return None
    return nh3.clean(
        raw,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        link_rel="noopener noreferrer nofollow",
    )
