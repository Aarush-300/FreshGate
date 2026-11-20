"""FreshGate Email Service for Watchtower Monitoring Reports.

Provides SMTP-based email delivery with HTML templates for scheduled reports.
"""

import asyncio
import logging
import smtplib
from datetime import datetime
from typing import List, Optional
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

try:
    import aiosmtplib
    HAS_AIOSMTPLIB = True
except ImportError:
    HAS_AIOSMTPLIB = False

from core.models import Claim
from core.config import FreshGateConfig

logger = logging.getLogger(__name__)


class EmailService:
    """Async email service for sending watchtower reports and alerts."""

    def __init__(self, config: FreshGateConfig):
        self.config = config

    def _is_configured(self) -> bool:
        """Check if SMTP is properly configured."""
        return bool(
            self.config.smtp_host
            and self.config.smtp_user
            and self.config.smtp_password
            and self.config.smtp_from
        )

    async def send_html(
        self,
        to: List[str],
        subject: str,
        html: str,
        text: Optional[str] = None,
    ) -> bool:
        """Send HTML email with optional plain text fallback."""
        if not self._is_configured():
            logger.warning("SMTP not configured, skipping email send")
            return False

        if not to:
            logger.warning("No recipients specified, skipping email send")
            return False

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.config.smtp_from
        msg["To"] = ", ".join(to)

        # Plain text version
        if text:
            msg.attach(MIMEText(text, "plain"))
        else:
            # Generate basic text from HTML (strip tags)
            import re
            text_content = re.sub(r"<[^>]+>", "", html)
            msg.attach(MIMEText(text_content, "plain"))

        # HTML version
        msg.attach(MIMEText(html, "html"))

        try:
            implicit_tls = self.config.smtp_tls and self.config.smtp_port == 465
            start_tls = self.config.smtp_tls and not implicit_tls
            if HAS_AIOSMTPLIB:
                await aiosmtplib.send(
                    msg,
                    hostname=self.config.smtp_host,
                    port=self.config.smtp_port,
                    username=self.config.smtp_user,
                    password=self.config.smtp_password,
                    use_tls=implicit_tls,
                    start_tls=start_tls,
                    timeout=10,
                )
            else:
                def _send_sync():
                    if implicit_tls:
                        server = smtplib.SMTP_SSL(self.config.smtp_host, self.config.smtp_port, timeout=10)
                    else:
                        server = smtplib.SMTP(self.config.smtp_host, self.config.smtp_port, timeout=10)
                        if start_tls:
                            server.starttls()
                    try:
                        if self.config.smtp_user and self.config.smtp_password:
                            server.login(self.config.smtp_user, self.config.smtp_password)
                        server.send_message(msg)
                    finally:
                        try:
                            server.quit()
                        except smtplib.SMTPException:
                            server.close()

                await asyncio.to_thread(_send_sync)

            logger.info("Email sent successfully to %s", ", ".join(to))
            return True
        except Exception as e:
            logger.error("Failed to send email: %s", e)
            return False

    async def send_watchtower_report(
        self,
        to: List[str],
        claims: List[Claim],
        alerts: List[dict],
    ) -> bool:
        """Send formatted watchtower monitoring report."""
        subject = f"🔍 FreshGate Watchtower Report - {datetime.now().strftime('%Y-%m-%d %H:%M')}"

        # Build HTML report
        html_parts = [
            """
            <!DOCTYPE html>
            <html>
            <head>
                <style>
                    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; max-width: 800px; margin: 0 auto; padding: 20px; }
                    .header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; border-radius: 12px 12px 0 0; }
                    .header h1 { margin: 0; font-size: 28px; font-weight: 600; }
                    .header p { margin: 10px 0 0; opacity: 0.9; }
                    .content { background: #fafafa; padding: 30px; border-radius: 0 0 12px 12px; border: 1px solid #e1e5e9; }
                    .summary { display: flex; gap: 20px; margin-bottom: 30px; flex-wrap: wrap; }
                    .stat { background: white; padding: 20px; border-radius: 8px; border: 1px solid #e1e5e9; flex: 1; min-width: 150px; }
                    .stat .label { font-size: 12px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; }
                    .stat .value { font-size: 24px; font-weight: 700; color: #333; margin-top: 4px; }
                    .stat.confirmed .value { color: #28a745; }
                    .stat.contradicted .value { color: #dc3545; }
                    .stat.unverifiable .value { color: #ffc107; }
                    .stat.alert .value { color: #dc3545; }
                    .section { margin-bottom: 30px; }
                    .section h2 { font-size: 18px; font-weight: 600; color: #333; border-bottom: 2px solid #667eea; padding-bottom: 8px; margin-bottom: 16px; }
                    .claim { background: white; border: 1px solid #e1e5e9; border-radius: 8px; padding: 20px; margin-bottom: 16px; }
                    .claim-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; }
                    .claim-type { background: #667eea; color: white; padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 600; text-transform: uppercase; }
                    .claim-score { font-size: 18px; font-weight: 700; }
                    .claim-score.high { color: #28a745; }
                    .claim-score.medium { color: #ffc107; }
                    .claim-score.low { color: #dc3545; }
                    .claim-text { font-size: 15px; color: #333; margin-bottom: 12px; line-height: 1.5; }
                    .claim-meta { display: flex; gap: 16px; font-size: 13px; color: #666; flex-wrap: wrap; }
                    .claim-meta span { display: flex; align-items: center; gap: 4px; }
                    .verdict-badge { padding: 4px 10px; border-radius: 4px; font-size: 12px; font-weight: 600; text-transform: capitalize; }
                    .verdict-badge.confirmed { background: #d4edda; color: #155724; }
                    .verdict-badge.contradicted { background: #f8d7da; color: #721c24; }
                    .verdict-badge.partially_supported { background: #fff3cd; color: #856404; }
                    .verdict-badge.unverifiable { background: #e2e3e5; color: #383d41; }
                    .alert { background: #fff3cd; border: 1px solid #ffeaa7; border-radius: 8px; padding: 16px; margin-bottom: 12px; }
                    .alert h3 { margin: 0 0 8px; font-size: 15px; color: #856404; }
                    .alert p { margin: 0; color: #666; font-size: 14px; }
                    .footer { text-align: center; padding: 20px; color: #999; font-size: 12px; }
                    .source-list { margin-top: 12px; font-size: 13px; }
                    .source-list a { color: #667eea; text-decoration: none; }
                    .source-list a:hover { text-decoration: underline; }
                </style>
            </head>
            <body>
            """,
            f"""
            <div class="header">
                <h1>🔍 FreshGate Watchtower Report</h1>
                <p>Automated monitoring summary • {datetime.now().strftime('%B %d, %Y at %H:%M UTC')}</p>
            </div>
            <div class="content">
            """,
        ]

        # Summary stats
        confirmed = sum(1 for c in claims if c.verdict == "confirmed")
        contradicted = sum(1 for c in claims if c.verdict == "contradicted")
        partially = sum(1 for c in claims if c.verdict == "partially_supported")
        unverifiable = sum(1 for c in claims if c.verdict == "unverifiable")

        html_parts.append(f"""
            <div class="summary">
                <div class="stat confirmed">
                    <div class="label">Confirmed</div>
                    <div class="value">{confirmed}</div>
                </div>
                <div class="stat contradicted">
                    <div class="label">Contradicted</div>
                    <div class="value">{contradicted}</div>
                </div>
                <div class="stat">
                    <div class="label">Partially Supported</div>
                    <div class="value">{partially}</div>
                </div>
                <div class="stat">
                    <div class="label">Unverifiable</div>
                    <div class="value">{unverifiable}</div>
                </div>
                <div class="stat alert">
                    <div class="label">Active Alerts</div>
                    <div class="value">{len(alerts)}</div>
                </div>
            </div>
        """)

        # Alerts section
        if alerts:
            html_parts.append("""
            <div class="section">
                <h2>⚠️ Active Alerts</h2>
            """)
            for alert in alerts:
                html_parts.append(f"""
                <div class="alert">
                    <h3>{alert.get('title', 'Alert')}</h3>
                    <p>{alert.get('message', '')}</p>
                </div>
                """)
            html_parts.append("</div>")

        # Claims detail
        if claims:
            html_parts.append("""
            <div class="section">
                <h2>📋 Monitored Claims</h2>
            """)
            for claim in claims:
                score = claim.score or 0
                score_class = "high" if score >= 70 else ("medium" if score >= 40 else "low")
                verdict_class = claim.verdict or "unknown"

                # Sources
                sources_html = ""
                if claim.sources:
                    sources_html = '<div class="source-list">Sources: '
                    source_links = []
                    for src in claim.sources[:3]:  # Limit to 3 sources
                        source_links.append(f'<a href="{src.url}" target="_blank">{src.domain}</a>')
                    sources_html += ", ".join(source_links)
                    if len(claim.sources) > 3:
                        sources_html += f" and {len(claim.sources) - 3} more"
                    sources_html += "</div>"

                html_parts.append(f"""
                <div class="claim">
                    <div class="claim-header">
                        <span class="claim-type">{claim.type}</span>
                        <span class="claim-score {score_class}">{score}/100</span>
                    </div>
                    <div class="claim-text">{claim.text}</div>
                    <div class="claim-meta">
                        <span><span class="verdict-badge {verdict_class}">{claim.verdict or 'unknown'}</span></span>
                        <span>📅 {claim.last_verified_at or 'Never'}</span>
                        <span>🔮 Flip prob: {getattr(claim, 'predicted_flip_probability', 'N/A')}</span>
                    </div>
                    {sources_html}
                </div>
                """)
            html_parts.append("</div>")

        html_parts.append("""
            <div class="footer">
                <p>This is an automated report from FreshGate Watchtower Monitoring.</p>
                <p>To adjust notification settings, visit your FreshGate dashboard.</p>
            </div>
            </body>
            </html>
        """)

        html = "\n".join(html_parts)
        return await self.send_html(to, subject, html)

    async def send_test_email(self, to: List[str]) -> bool:
        """Send a test email to verify SMTP configuration."""
        subject = "🧪 FreshGate Test Email"
        html = """
        <!DOCTYPE html>
        <html>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 20px;">
            <h2 style="color: #667eea;">FreshGate SMTP Test</h2>
            <p>This is a test email from FreshGate Watchtower.</p>
            <p>If you received this, your SMTP configuration is working correctly! ✅</p>
            <hr style="border: none; border-top: 1px solid #e1e5e9; margin: 20px 0;">
            <p style="color: #999; font-size: 12px;">Sent at """ + datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC') + """</p>
        </body>
        </html>
        """
        return await self.send_html(to, subject, html)


def get_email_service(config: Optional[FreshGateConfig] = None) -> EmailService:
    """Get or create email service instance."""
    if config is None:
        from core.config import get_config
        config = get_config()
    return EmailService(config)