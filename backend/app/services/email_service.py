import smtplib
import structlog
from email.message import EmailMessage

from app.core.config import settings

logger = structlog.get_logger(__name__)


class EmailService:
    """Thin wrapper around SMTP send, provider-agnostic per Master Plan §0.

    Any SMTP-compatible transactional provider (Postmark, SES, Resend, Brevo,
    etc.) can be plugged in purely via SMTP_* config — no code change needed.
    In Phase 2+, high-volume sends (bulk notifications) should be dispatched
    through a Celery task rather than calling this synchronously from a
    request handler.
    """

    def send(self, to: str, subject: str, html_body: str, text_body: str | None = None) -> bool:
        """Returns True if the email was sent (or dev-mode-printed), False if
        a real send attempt failed. Delivery stays best-effort — this NEVER
        raises — but the return value lets a caller that specifically needs
        to know (e.g. admin invite, where "no delivery" should be surfaced
        in the API response) check without changing the no-raise contract
        every other caller already relies on."""
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = f"{settings.EMAIL_FROM_NAME} <{settings.EMAIL_FROM_ADDRESS}>"
        message["To"] = to
        message.set_content(text_body or _strip_html(html_body))
        message.add_alternative(html_body, subtype="html")

        if settings.ENVIRONMENT == "development" and not settings.SMTP_USERNAME:
            # No SMTP provider configured — print the full email to the
            # console instead of silently dropping it, so a developer can
            # copy a real verification/reset/invite link out of the backend
            # log and complete the flow exactly as a real recipient would.
            # Switches back to the real smtplib path below automatically
            # the moment SMTP_USERNAME is set; no caller changes needed.
            divider = "=" * 70
            links = _extract_links(html_body)
            detail_lines = []
            for href in links:
                detail_lines.append(f"Link:    {href}")
                token = _extract_token(href)
                if token:
                    detail_lines.append(f"Token:   {token}")
            details_block = "\n".join(detail_lines) + "\n" if detail_lines else ""
            print(
                f"\n{divider}\n"
                f"[DEV EMAIL] SMTP not configured — printing instead of sending\n"
                f"{divider}\n"
                f"To:      {to}\n"
                f"Subject: {subject}\n"
                f"{details_block}"
                f"{divider}\n"
                f"{text_body or _strip_html(html_body)}\n"
                f"{divider}\n"
            )
            logger.info("email.dev_mode_printed", to=to, subject=subject, links=links)
            return True

        try:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as smtp:
                if settings.SMTP_USE_TLS:
                    smtp.starttls()
                if settings.SMTP_USERNAME and settings.SMTP_PASSWORD:
                    smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                smtp.send_message(message)
            logger.info("email.sent", to=to, subject=subject)
            return True
        except Exception:
            # Email delivery is best-effort, not a precondition for the
            # caller's own action to succeed — registration/password-reset/
            # invite already commit their DB state before calling send(), so
            # a provider outage or bad credentials must not turn into a 500
            # for the caller or leave that DB state half-finished. Logged
            # with full detail (exc_info) for an admin to notice and fix the
            # provider config; the caller gets on with its own response.
            logger.exception("email.send_failed", to=to, subject=subject)
            return False

    def send_verification_email(self, to: str, full_name: str, token: str) -> bool:
        verify_url = f"{settings.FRONTEND_URL}/verify-email?token={token}"
        html = f"""
        <p>Hello {full_name},</p>
        <p>Thanks for registering with {settings.EMAIL_FROM_NAME}. Please verify your
        email address to activate your account:</p>
        <p><a href="{verify_url}">Verify my email</a></p>
        <p>This link expires in {settings.EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS} hours.
        If you did not create this account, you can ignore this email.</p>
        """
        return self.send(to, f"Verify your {settings.EMAIL_FROM_NAME} account", html)

    def send_password_reset_email(self, to: str, full_name: str, token: str) -> bool:
        reset_url = f"{settings.FRONTEND_URL}/reset-password?token={token}"
        html = f"""
        <p>Hello {full_name},</p>
        <p>We received a request to reset your password. Click below to choose a new one:</p>
        <p><a href="{reset_url}">Reset my password</a></p>
        <p>This link expires in {settings.PASSWORD_RESET_TOKEN_EXPIRE_HOURS} hours.
        If you did not request this, you can ignore this email.</p>
        """
        return self.send(to, f"Reset your {settings.EMAIL_FROM_NAME} password", html)

    def send_request_submitted_email(
        self, to: str, *, admin_name: str, requester_name: str, dataset_title: str
    ) -> bool:
        review_url = f"{settings.FRONTEND_URL}/admin"
        html = f"""
        <p>Hello {admin_name},</p>
        <p><b>{requester_name}</b> requested access to <b>&ldquo;{dataset_title}&rdquo;</b>.</p>
        <p><a href="{review_url}">Review this request</a></p>
        """
        return self.send(to, f"New dataset access request — {dataset_title}", html)

    def send_request_approved_email(self, to: str, *, full_name: str, dataset_title: str) -> bool:
        dashboard_url = f"{settings.FRONTEND_URL}/dashboard"
        html = f"""
        <p>Hello {full_name},</p>
        <p>Your access request for <b>&ldquo;{dataset_title}&rdquo;</b> has been approved.</p>
        <p><a href="{dashboard_url}">View it in your dashboard</a></p>
        """
        return self.send(to, f"Access approved — {dataset_title}", html)

    def send_request_rejected_email(
        self, to: str, *, full_name: str, dataset_title: str, reason: str
    ) -> bool:
        dashboard_url = f"{settings.FRONTEND_URL}/dashboard"
        html = f"""
        <p>Hello {full_name},</p>
        <p>Your access request for <b>&ldquo;{dataset_title}&rdquo;</b> was not approved.</p>
        <p><b>Reason:</b> {reason}</p>
        <p>You may submit a new request from the <a href="{dashboard_url}">dataset catalog</a>
        with adjusted scope or justification.</p>
        """
        return self.send(to, f"Access request update — {dataset_title}", html)

    def send_grant_expiring_email(
        self, to: str, *, full_name: str, dataset_title: str, expires_at: str
    ) -> bool:
        dashboard_url = f"{settings.FRONTEND_URL}/dashboard"
        html = f"""
        <p>Hello {full_name},</p>
        <p>Your access to <b>&ldquo;{dataset_title}&rdquo;</b> expires on <b>{expires_at}</b>.</p>
        <p>Visit your <a href="{dashboard_url}">dashboard</a> if you need continued access.</p>
        """
        return self.send(to, f"Access expiring soon — {dataset_title}", html)

    def send_admin_invite_email(
        self, to: str, full_name: str, token: str, *, is_admin: bool
    ) -> bool:
        set_password_url = f"{settings.FRONTEND_URL}/set-password?token={token}"
        role_desc = "an administrator" if is_admin else "a researcher"
        html = f"""
        <p>Hello {full_name},</p>
        <p>An administrator has created {role_desc} account for you on
        {settings.EMAIL_FROM_NAME}.</p>
        <p><a href="{set_password_url}">Set your password to activate your account</a></p>
        <p>This link expires in {settings.INVITE_TOKEN_EXPIRE_HOURS} hours.</p>
        """
        return self.send(to, f"You've been invited to {settings.EMAIL_FROM_NAME}", html)

    def send_support_ticket_created_email(
        self, to: str, *, subject: str, requester_name: str, requester_email: str, message: str
    ) -> bool:
        html = f"""
        <p>New support ticket from <b>{requester_name}</b> ({requester_email}):</p>
        <p><b>Subject:</b> {subject}</p>
        <p>{message}</p>
        """
        return self.send(to, f"New support ticket — {subject}", html)

    def send_contact_reply_email(
        self,
        to: str,
        *,
        name: str,
        original_subject: str,
        original_message: str,
        reply_message: str,
    ) -> bool:
        # `name`/`original_subject`/`original_message` are public, unauthenticated
        # visitor input (the /contact form has no auth) — escape before
        # interpolating into HTML, unlike this file's other send_* methods,
        # which only ever embed already-trusted server-side/authenticated values.
        import html as html_lib

        safe_name = html_lib.escape(name)
        safe_subject = html_lib.escape(original_subject)
        safe_original = html_lib.escape(original_message).replace("\n", "<br>")
        safe_reply = html_lib.escape(reply_message).replace("\n", "<br>")
        html = f"""
        <p>Hello {safe_name},</p>
        <p>Thanks for contacting {settings.EMAIL_FROM_NAME}. Here's our reply to your message:</p>
        <blockquote style="border-left:3px solid #ccc;margin:0 0 1em;padding-left:1em;color:#333;">
        {safe_reply}
        </blockquote>
        <p style="color:#888;font-size:0.85em;">Your original message ({safe_subject}):</p>
        <blockquote style="border-left:3px solid #eee;margin:0;padding-left:1em;color:#888;font-size:0.85em;">
        {safe_original}
        </blockquote>
        """
        return self.send(to, f"Re: {original_subject}", html)


def _strip_html(html: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", html).strip()


def _extract_links(html: str) -> list[str]:
    import re

    return re.findall(r'href="([^"]+)"', html)


def _extract_token(url: str) -> str | None:
    from urllib.parse import parse_qs, urlparse

    query = parse_qs(urlparse(url).query)
    values = query.get("token")
    return values[0] if values else None


email_service = EmailService()
