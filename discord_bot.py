"""
ACS Discord Bot - DM Mode
- 위진수에게 DM으로 알림 전송
- DM에서 버튼으로 승인/거절
- 슬래시 커맨드로 상태 확인

Run: python discord_bot.py
Requires: pip install discord.py
"""

import sys
import logging
import json
import yaml

import discord
from discord.ext import commands, tasks
from discord import app_commands

from core.db import get_thread_connection, init_schema
from core.message_bus import MessageBus, Message, MsgType, Urgency
from core.glasswing import GlasswingManager, STAGES
from core.discord_logger import attach_discord_logger

logger = logging.getLogger(__name__)

URGENCY_COLOR = {
    Urgency.LOW:      discord.Color.green(),
    Urgency.MEDIUM:   discord.Color.yellow(),
    Urgency.HIGH:     discord.Color.orange(),
    Urgency.ABSOLUTE: discord.Color.red(),
}
URGENCY_EMOJI = {
    Urgency.LOW:      "🟢",
    Urgency.MEDIUM:   "🟡",
    Urgency.HIGH:     "🟠",
    Urgency.ABSOLUTE: "🔴",
}


# ──────────────────────────────────────────
# Approval Button View
# ──────────────────────────────────────────

class ApprovalView(discord.ui.View):
    def __init__(self, alert_id: int, bus: MessageBus, payload: dict):
        super().__init__(timeout=None)
        self.alert_id = alert_id
        self.bus = bus
        self.payload = payload

    @discord.ui.button(label="✅ 승인", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._respond(interaction, approved=True)

    @discord.ui.button(label="❌ 거절", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._respond(interaction, approved=False)

    async def _respond(self, interaction: discord.Interaction, approved: bool):
        self.bus.send(Message(
            from_agent="hotl_human",
            to_agent="board",
            msg_type=MsgType.APPROVAL_RES,
            payload={
                **self.payload,
                "approved": approved,
                "reason": f"위진수 {'승인' if approved else '거절'} (Discord DM)",
            },
            priority=1,
        ))

        self.bus.conn.execute(
            "UPDATE hotl_alerts SET status='acknowledged' WHERE id=?",
            (self.alert_id,)
        )
        self.bus.conn.commit()

        for child in self.children:
            child.disabled = True

        label = "✅ 승인됨" if approved else "❌ 거절됨"
        await interaction.response.edit_message(content=f"**{label}**", view=self)
        logger.info(f"[Bot] alert_id={self.alert_id} -> {'approved' if approved else 'rejected'}")


# ──────────────────────────────────────────
# Bot
# ──────────────────────────────────────────

class ACSBot(commands.Bot):
    def __init__(self, config: dict):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

        self.config = config
        self.db_path = config["system"]["db_path"]
        self.owner_id = int(config["hotl"]["discord"]["owner_id"])

        self.conn = get_thread_connection(self.db_path)
        self.bus = MessageBus(self.conn)
        self._owner_dm: discord.DMChannel = None

    async def setup_hook(self):
        await self.tree.sync()
        self.poll_alerts.start()
        logger.info("[Bot] Ready")

    async def on_ready(self):
        logger.info(f"[Bot] Logged in as {self.user}")
        # 위진수 DM 채널 미리 열어두기
        owner = await self.fetch_user(self.owner_id)
        self._owner_dm = await owner.create_dm()
        # 온보딩 이미지 전송
        import os
        image_path = os.path.join(os.path.dirname(__file__), "assets", "owner.jpg")
        if os.path.exists(image_path):
            await self._owner_dm.send(
                "🤖 **ACS Bot online.** DM 알림 준비 완료.",
                file=discord.File(image_path)
            )
        else:
            await self._owner_dm.send("🤖 **ACS Bot online.** DM 알림 준비 완료.")
        attach_discord_logger(self)
        logger.info("ACS Bot connected — DM logging active")
        logger.info(f"[Bot] DM channel ready for owner (id={self.owner_id})")

    async def send_dm(self, content: str = None, embed: discord.Embed = None, view: discord.ui.View = None):
        """위진수에게 DM 전송."""
        if not self._owner_dm:
            owner = await self.fetch_user(self.owner_id)
            self._owner_dm = await owner.create_dm()
        await self._owner_dm.send(content=content, embed=embed, view=view)

    async def log_dm(self, message: str):
        """중요 로그를 DM으로 전송. 짧고 텍스트만."""
        try:
            await self.send_dm(content=f"`{message}`")
        except Exception as e:
            logger.error(f"[Bot] log_dm 실패: {e}")

    # ──────────────────────────────────────────
    # Alert Polling
    # ──────────────────────────────────────────

    @tasks.loop(seconds=5)
    async def poll_alerts(self):
        try:
            alerts = self.conn.execute(
                "SELECT * FROM hotl_alerts WHERE status='pending' ORDER BY id ASC"
            ).fetchall()
            for alert in alerts:
                await self._dispatch_alert(alert)
        except Exception as e:
            logger.error(f"[Bot] poll error: {e}")

    @poll_alerts.before_loop
    async def before_poll(self):
        await self.wait_until_ready()

    async def _dispatch_alert(self, alert):
        urgency = alert["urgency"]
        is_approval = ("승인" in alert["title"] or
                       "approval" in alert["title"].lower() or
                       urgency == Urgency.ABSOLUTE)

        embed = discord.Embed(
            title=f"{URGENCY_EMOJI.get(urgency, '⚪')} {alert['title']}",
            description=str(alert["body"])[:1000],
            color=URGENCY_COLOR.get(urgency, discord.Color.greyple()),
        )
        embed.set_footer(text=f"from: {alert['from_agent']} | id: {alert['id']}")

        if is_approval:
            try:
                payload = json.loads(alert["body"])
            except Exception:
                payload = {"raw": alert["body"]}

            view = ApprovalView(
                alert_id=alert["id"],
                bus=self.bus,
                payload=payload,
            )
            await self.send_dm(embed=embed, view=view)
        else:
            await self.send_dm(embed=embed)

        # 전송 완료 표시
        self.conn.execute(
            "UPDATE hotl_alerts SET status='sent' WHERE id=?", (alert["id"],)
        )
        self.conn.commit()

    # ──────────────────────────────────────────
    # DM 메시지 처리 (텍스트 명령)
    # ──────────────────────────────────────────

    async def on_message(self, message: discord.Message):
        if message.author.id != self.owner_id:
            return
        if not isinstance(message.channel, discord.DMChannel):
            return

        text = message.content.strip().lower()

        if text in ("status", "상태"):
            await self._cmd_status(message.channel)
        elif text.startswith("stage "):
            await self._cmd_stage(message.channel, text.split(" ", 1)[1].strip())
        elif text in ("alerts", "알림"):
            await self._cmd_alerts(message.channel)
        elif text in ("help", "도움말"):
            await message.channel.send(
                "**ACS Bot 명령어**\n"
                "`status` — 에이전트 상태 + 포트폴리오\n"
                "`alerts` — 미처리 알림 목록\n"
                "`stage 0~4` — Glasswing 자율성 단계 수동 설정\n"
                "`help` — 이 메시지"
            )

    # ──────────────────────────────────────────
    # 명령어 구현
    # ──────────────────────────────────────────

    async def _cmd_status(self, channel):
        CRITICAL = {"board", "system_cfo"}

        # --- Agents ---
        agents = self.conn.execute(
            "SELECT agent_name, status, updated_at FROM agent_states ORDER BY agent_name"
        ).fetchall()
        pending = self.conn.execute(
            "SELECT COUNT(*) FROM hotl_alerts WHERE status='pending'"
        ).fetchone()[0]

        agent_embed = discord.Embed(title="🏢 ACS Status", color=discord.Color.blue())
        for a in agents:
            if a["status"] == "stopped":
                emoji = "⚫"
            else:
                try:
                    row = self.conn.execute(
                        "SELECT CAST((julianday('now') - julianday(?)) * 86400 AS INTEGER)",
                        (a["updated_at"],)
                    ).fetchone()
                    elapsed = row[0] if row else 9999
                    timeout = 60
                    healthy = elapsed < timeout
                except Exception:
                    healthy = True
                emoji = "🟢" if healthy else "🔴"

            agent_embed.add_field(
                name=f"{emoji} {a['agent_name']}",
                value=f"`{a['status']}` | {a['updated_at']}",
                inline=True,
            )
        agent_embed.set_footer(text=f"Pending alerts: {pending}")
        await channel.send(embed=agent_embed)

        # --- Portfolio ---
        await self._cmd_portfolio(channel)

    async def _cmd_portfolio(self, channel):
        state = self.bus.get_agent_state("system_cfo") or {}

        embed = discord.Embed(title="💰 Portfolio", color=discord.Color.gold())
        embed.add_field(name="총 자본",   value=f"{state.get('total_capital', 0):,} KRW", inline=True)
        embed.add_field(name="운용 가능", value=f"{state.get('deployable', 0):,} KRW",    inline=True)
        embed.add_field(name="총 투자",   value=f"{state.get('total_invested', 0):,} KRW", inline=True)
        embed.add_field(name="총 회수",   value=f"{state.get('total_returned', 0):,} KRW", inline=True)
        embed.add_field(name="Exit",      value=f"{state.get('exit_count', 0)}건",          inline=True)
        embed.add_field(name="실패",      value=f"{state.get('failure_count', 0)}건",        inline=True)
        embed.add_field(name="활성 회사", value=f"{state.get('active_companies', 0)}개",    inline=True)

        sector_db = state.get("sector_db", {})
        if sector_db:
            text = "\n".join(
                f"• {s}: 성공률 {d['success_rate']*100:.0f}% ({d['total']}건)"
                for s, d in sector_db.items()
            )
            embed.add_field(name="업종 DB", value=text, inline=False)

        await channel.send(embed=embed)

    async def _cmd_stage(self, channel, stage_str: str):
        try:
            stage = int(stage_str)
            if stage < 0 or stage > 4:
                await channel.send("Stage는 0~4 사이로 입력하세요.")
                return
            gw = GlasswingManager(self.db_path)
            gw.set_stage(stage, reason="위진수 수동 설정")
            policy = STAGES[stage]
            await channel.send(
                f"Glasswing Stage {stage}로 설정됨\n"
                f"**{policy.name}**: {policy.description}\n"
                f"자율 한도: {policy.auto_approve_cost_ratio*100:.0f}% | 알림 필터: {policy.hotl_urgency_filter.upper()}"
            )
        except ValueError:
            await channel.send("사용법: `stage 0` ~ `stage 4`")

    async def _cmd_alerts(self, channel):
        alerts = self.conn.execute(
            "SELECT * FROM hotl_alerts WHERE status='pending' ORDER BY id DESC LIMIT 10"
        ).fetchall()

        if not alerts:
            await channel.send("✅ 미처리 알림 없음")
            return

        embed = discord.Embed(title=f"⚠️ Pending Alerts ({len(alerts)})", color=discord.Color.orange())
        for a in alerts:
            embed.add_field(
                name=f"{URGENCY_EMOJI.get(a['urgency'], '⚪')} {a['title'][:50]}",
                value=f"`{a['from_agent']}` | {a['created_at']}",
                inline=False,
            )
        await channel.send(embed=embed)


# ──────────────────────────────────────────
# Entry
# ──────────────────────────────────────────

def main():
    import os
    os.makedirs("logs", exist_ok=True)
    _handlers = [logging.FileHandler("logs/discord.log", encoding="utf-8")]
    try:
        import io, sys
        _handlers.insert(0, logging.StreamHandler(io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")))
    except AttributeError:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=_handlers,
    )

    with open("config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    token = config["hotl"]["discord"]["token"]
    owner_id = config["hotl"]["discord"].get("owner_id", 0)

    if token == "YOUR_BOT_TOKEN":
        print("config.yaml에서 Discord bot token을 설정하세요.")
        sys.exit(1)
    if not owner_id:
        print("config.yaml에서 owner_id (본인 Discord 유저 ID)를 설정하세요.")
        sys.exit(1)

    bot = ACSBot(config)
    bot.run(token)


if __name__ == "__main__":
    main()
