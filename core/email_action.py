"""
EmailAction - CEO 외부 액션 이메일 모듈.
CEO가 작성한 초안을 위진수에게 전달하거나
Glasswing Stage에 따라 자동 발송.

Gmail SMTP 사용 (앱 비밀번호 필요).
"""

import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class EmailDraft:
    """CEO가 작성한 이메일 초안."""
    subject: str
    body: str
    to: str                        # 수신자
    action_type: str               # 어떤 외부 액션인지
    company_id: str
    ceo_name: str
    estimated_revenue: int = 0     # 예상 수익
    brand_name: str = ""           # 브랜드명 (서명에 표시)


class EmailAction:
    def __init__(self, config: dict):
        """
        config 예시:
          email:
            sender: "acs@gmail.com"
            app_password: "xxxx xxxx xxxx xxxx"
            owner_email: "위진수@gmail.com"
        """
        self.cfg = config.get("email", {})
        self.sender = self.cfg.get("sender", "")
        self.app_password = self.cfg.get("app_password", "")
        self.owner_email = self.cfg.get("owner_email", "")
        self.owner_name = self.cfg.get("owner_name", "")
        self.reply_to = self.cfg.get("reply_to", "") or self.owner_email
        self.enabled = bool(self.sender and self.app_password and self.owner_email)

        if not self.enabled:
            logger.warning("[Email] 설정 없음 — config.yaml에 email 섹션 추가 필요")

    # ──────────────────────────────────────────
    # 발송
    # ──────────────────────────────────────────

    def send(self, draft: EmailDraft) -> bool:
        """이메일 실제 발송."""
        if not self.enabled:
            logger.warning(f"[Email] 발송 스킵 (설정 없음): {draft.subject}")
            return False

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = draft.subject
            msg["From"] = f"{self.owner_name} <{self.sender}>" if self.owner_name else self.sender
            msg["To"] = draft.to
            if self.reply_to:
                msg["Reply-To"] = self.reply_to

            # 서명 추가
            signature = ""
            if self.owner_name:
                signature = f"\n\n--\n{self.owner_name}"
                if draft.brand_name:
                    signature += f"\n{draft.brand_name}"
                if self.reply_to:
                    signature += f"\n{self.reply_to}"

            full_body = f"{draft.body}{signature}"

            msg.attach(MIMEText(full_body, "plain", "utf-8"))

            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(self.sender, self.app_password)
                server.sendmail(self.sender, draft.to, msg.as_string())

            logger.info(f"[Email] 발송 완료: {draft.subject} → {draft.to}")
            return True

        except Exception as e:
            logger.error(f"[Email] 발송 실패: {e}")
            return False

    def send_to_owner(self, draft: EmailDraft) -> bool:
        """위진수에게 초안 전달 (Stage 1~2용)."""
        review_draft = EmailDraft(
            subject=f"[ACS 검토 요청] {draft.subject}",
            body=(
                f"CEO {draft.ceo_name}이 다음 이메일 발송을 요청합니다.\n\n"
                f"수신자: {draft.to}\n"
                f"예상 수익: {draft.estimated_revenue:,}원\n\n"
                f"{'='*40}\n"
                f"제목: {draft.subject}\n\n"
                f"{draft.body}\n"
                f"{'='*40}\n\n"
                f"승인: Discord DM에서 승인 버튼을 눌러주세요."
            ),
            to=self.owner_email,
            action_type="review_request",
            company_id=draft.company_id,
            ceo_name=draft.ceo_name,
        )
        return self.send(review_draft)

    # ──────────────────────────────────────────
    # 설정 확인
    # ──────────────────────────────────────────

    def test_connection(self) -> bool:
        """Gmail 연결 테스트."""
        if not self.enabled:
            return False
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(self.sender, self.app_password)
            logger.info("[Email] Gmail 연결 성공")
            return True
        except Exception as e:
            logger.error(f"[Email] Gmail 연결 실패: {e}")
            return False
