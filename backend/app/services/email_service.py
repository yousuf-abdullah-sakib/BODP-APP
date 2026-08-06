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

    def send(self, to: str, subject: str, html_body: str, text_body: str | None = None) -> None:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = f"{settings.EMAIL_FROM_NAME} <{settings.EMAIL_FROM_ADDRESS}>"
        message["To"] = to
        message.set_content(text_body or _strip_html(html_body))
        message.add_alternative(html_body, subtype="html")

        if settings.ENVIRONMENT == "development" and not settings.SMTP_USERNAME:
            logger.info("email.dev_mode_skipped_send", to=to, subject=subject)
            return

        try:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as smtp:
                if settings.SMTP_USE_TLS:
                    smtp.starttls()
                if settings.SMTP_USERNAME and settings.SMTP_PASSWORD:
                    smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                smtp.send_message(message)
            logger.info("email.sent", to=to, subject=subject)
        except Exception:
            logger.exception("email.send_failed", to=to, subject=subject)
            raise

    def send_verification_email(self, to: str, full_name: str, token: str) -> None:
        verify_url = f"{settings.FRONTEND_URL}/verify-email?token={token}"
        html = f"""
        <p>Hello {full_name},</p>
        <p>Thanks for registering with {settings.EMAIL_FROM_NAME}. Please verify your
        email address to activate your account:</p>
        <p><a href="{verify_url}">Verify my email</a></p>
        <p>This link expires in {settings.EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS} hours.
        If you did not create this account, you can ignore this email.</p>
        """
        self.send(to, f"Verify your {settings.EMAIL_FROM_NAME} account", html)

    def send_password_reset_email(self, to: str, full_name: str, token: str) -> None:
        reset_url = f"{settings.FRONTEND_URL}/reset-password?token={token}"
        html = f"""
        <p>Hello {full_name},</p>
        <p>We received a request to reset your password. Click below to choose a new one:</p>
        <p><a href="{reset_url}">Reset my password</a></p>
        <p>This link expires in {settings.PASSWORD_RESET_TOKEN_EXPIRE_HOURS} hours.
        If you did not request this, you can ignore this email.</p>
        """
        self.send(to, f"Reset your {settings.EMAIL_FROM_NAME} password", html)

    def send_request_submitted_email(
        self, to: str, *, admin_name: str, requester_name: str, dataset_title: str
    ) -> None:
        review_url = f"{settings.FRONTEND_URL}/admin"
        html = f"""
        <p>Hello {admin_name},</p>
        <p><b>{requester_name}</b> requested access to <b>&ldquo;{dataset_title}&rdquo;</b>.</p>
        <p><a href="{review_url}">Review this request</a></p>
        """
        self.send(to, f"New dataset access request — {dataset_title}", html)

    def send_request_approved_email(self, to: str, *, full_name: str, dataset_title: str) -> None:
        dashboard_url = f"{settings.FRONTEND_URL}/dashboard"
        html = f"""
        <p>Hello {full_name},</p>
        <p>Your access request for <b>&ldquo;{dataset_title}&rdquo;</b> has been approved.</p>
        <p><a href="{dashboard_url}">View it in your dashboard</a></p>
        """
        self.send(to, f"Access approved — {dataset_title}", html)

    def send_request_rejected_email(
        self, to: str, *, full_name: str, dataset_title: str, reason: str
    ) -> None:
        dashboard_url = f"{settings.FRONTEND_URL}/dashboard"
        html = f"""
        <p>Hello {full_name},</p>
        <p>Your access request for <b>&ldquo;{dataset_title}&rdquo;</b> was not approved.</p>
        <p><b>Reason:</b> {reason}</p>
        <p>You may submit a new request from the <a href="{dashboard_url}">dataset catalog</a>
        with adjusted scope or justification.</p>
        """
        self.send(to, f"Access request update — {dataset_title}", html)

    def send_grant_expiring_email(
        self, to: str, *, full_name: str, dataset_title: str, expires_at: str
    ) -> None:
        dashboard_url = f"{settings.FRONTEND_URL}/dashboard"
        html = f"""
        <p>Hello {full_name},</p>
        <p>Your access to <b>&ldquo;{dataset_title}&rdquo;</b> expires on <b>{expires_at}</b>.</p>
        <p>Visit your <a href="{dashboard_url}">dashboard</a> if you need continued access.</p>
        """
        self.send(to, f"Access expiring soon — {dataset_title}", html)


def _strip_html(html: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", html).strip()


email_service = EmailService()
