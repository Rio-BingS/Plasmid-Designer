"""邮件发送（注册邮箱验证码用）

四种发送渠道，由 MAIL_PROVIDER 选择：
- console（默认）: 验证码打进后端日志——本地开发/演示零配置可用
- smtp:            任意邮箱服务商 SMTP（QQ/163/Gmail/Outlook 授权码等，零额外注册）
- resend:          Resend API（免费 3000 封/月、100 封/天，邮箱注册无需信用卡）
- brevo:           Brevo API（免费 300 封/天，邮箱注册无需信用卡）

发送在线程池中执行（SMTP/HTTP 都可能阻塞事件循环）。
发送失败不抛出——注册流程不因邮件渠道故障而中断，返回 False 由调用方提示重发。
"""

import logging
import re
import smtplib
import urllib.error
import urllib.request
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

from starlette.concurrency import run_in_threadpool

from app.config import settings

logger = logging.getLogger("plasmid_designer.mailer")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def verification_email_html(code: str, minutes: int = 10) -> str:
    return f"""
    <div style="font-family:system-ui,-apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;max-width:480px;margin:0 auto;padding:24px;">
      <h2 style="margin:0 0 12px;">Plasmid Designer 注册验证码</h2>
      <p style="color:#555;margin:0 0 16px;">您正在注册 Plasmid Designer 账号。请使用以下验证码完成邮箱验证：</p>
      <div style="font-size:32px;font-weight:700;letter-spacing:8px;color:#4f46e5;background:#f5f5ff;border-radius:8px;padding:16px;text-align:center;">{code}</div>
      <p style="color:#999;font-size:13px;margin:16px 0 0;">验证码 {minutes} 分钟内有效。若非本人操作，请忽略本邮件。</p>
    </div>"""


async def send_email(to: str, subject: str, html: str) -> bool:
    """按 MAIL_PROVIDER 分发。返回是否发送成功（失败已记录日志）。"""
    if not EMAIL_RE.match(to or ""):
        logger.warning("收件地址不合法，跳过发送: %r", to)
        return False
    provider = (settings.MAIL_PROVIDER or "console").strip().lower()
    try:
        if provider == "console":
            return await _send_console(to, subject, html)
        if provider == "smtp":
            return await run_in_threadpool(_send_smtp, to, subject, html)
        if provider == "resend":
            return await run_in_threadpool(_send_resend, to, subject, html)
        if provider == "brevo":
            return await run_in_threadpool(_send_brevo, to, subject, html)
        logger.error("未知 MAIL_PROVIDER: %s（可选 console/smtp/resend/brevo）", provider)
        return False
    except Exception:
        logger.exception("邮件发送失败（provider=%s, to=%s）", provider, to)
        return False


# ==================== 各渠道实现（阻塞函数，线程池内调用） ====================

def _send_console(to: str, subject: str, html: str) -> bool:
    import re as _re
    text = _re.sub(r"<[^>]+>", " ", html)
    text = _re.sub(r"\s+", " ", text).strip()
    logger.info("[MAIL:console] to=%s subject=%s | %s", to, subject, text)
    print(f"✉️ [MAIL:console] 收件人 {to} · {subject} · {text}")
    return True


def _send_smtp(to: str, subject: str, html: str) -> bool:
    if not settings.SMTP_HOST or not settings.SMTP_USER:
        logger.error("MAIL_PROVIDER=smtp 但未配置 SMTP_HOST/SMTP_USER")
        return False
    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((Header("Plasmid Designer", "utf-8").encode(), settings.MAIL_FROM or settings.SMTP_USER))
    msg["To"] = to
    if settings.SMTP_SSL:
        with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as s:
            s.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            s.sendmail(settings.SMTP_USER, [to], msg.as_string())
    else:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as s:
            s.starttls()
            s.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            s.sendmail(settings.SMTP_USER, [to], msg.as_string())
    return True


def _send_resend(to: str, subject: str, html: str) -> bool:
    import urllib.request

    if not settings.RESEND_API_KEY:
        logger.error("MAIL_PROVIDER=resend 但未配置 RESEND_API_KEY")
        return False
    body = __import__("json").dumps({
        "from": settings.MAIL_FROM,
        "to": [to],
        "subject": subject,
        "html": html,
    }).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails", data=body, method="POST",
        headers={
            "Authorization": f"Bearer {settings.RESEND_API_KEY}",
            "Content-Type": "application/json",
            # Resend 前面的 Cloudflare 会按 UA 拦截默认的 Python-urllib（error 1010）
            "User-Agent": "PlasmidDesigner/2.2",
        })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        logger.error("Resend 返回 %s: %s", e.code, (e.read() or b"").decode(errors="replace")[:300])
        return False


def _send_brevo(to: str, subject: str, html: str) -> bool:
    import urllib.request

    if not settings.BREVO_API_KEY:
        logger.error("MAIL_PROVIDER=brevo 但未配置 BREVO_API_KEY")
        return False
    body = __import__("json").dumps({
        "sender": {"name": "Plasmid Designer", "email": _sender_email()},
        "to": [{"email": to}],
        "subject": subject,
        "htmlContent": html,
    }).encode()
    req = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email", data=body, method="POST",
        headers={
            "api-key": settings.BREVO_API_KEY,
            "Content-Type": "application/json",
            "accept": "application/json",
            "User-Agent": "PlasmidDesigner/2.2",
        })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        logger.error("Brevo 返回 %s: %s", e.code, (e.read() or b"").decode(errors="replace")[:300])
        return False


def _sender_email() -> str:
    """MAIL_FROM 形如 'Name <a@b.com>' 时提取纯地址（Brevo sender.email 要求纯地址）"""
    m = re.search(r"<([^>]+)>", settings.MAIL_FROM or "")
    return m.group(1) if m else (settings.MAIL_FROM or settings.SMTP_USER)
