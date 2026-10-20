import os
import requests
from src.common.models import Transaction
from src.common.logger import get_logger

logger = get_logger("TelegramAlert")

class TelegramAlertDispatcher:
    """
    Dispatches formatted high-priority alerts to a Telegram Group / Channel.
    """

    def __init__(self, token: str = None, chat_id: str = None, enabled: bool = True):
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self.enabled = enabled and bool(self.token) and bool(self.chat_id) and self.token != "YOUR_TELEGRAM_BOT_TOKEN_HERE"

        if self.enabled:
            logger.info("Telegram Alerting is ENABLED.")
        else:
            logger.info("Telegram Alerting is DISABLED (Token or Chat ID not configured).")

    def send_alert(self, tx: Transaction) -> bool:
        if not self.enabled:
            logger.info(f"[SIMULATED ALERT] TX {tx.tx_id} | Score: {tx.risk_score} | Reasons: {', '.join(tx.reasons)}")
            return True

        reasons_list = "\n".join([f"  • {r}" for r in tx.reasons])
        message = (
            f"🚨 *[FRAUD ALERT - PAYMENT SWITCH]* 🚨\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• *Risk Score*   : `{tx.risk_score}/100` (CRITICAL)\n"
            f"• *Tx ID*        : `{tx.tx_id}`\n"
            f"• *Time (UTC)*   : `{tx.timestamp.strftime('%Y-%m-%d %H:%M:%S')}`\n"
            f"• *Channel*      : `{tx.channel}`\n"
            f"• *Source Inst*  : *{tx.source_bank}* (`{tx.source_account}`)\n"
            f"• *Dest Inst*    : *{tx.dest_bank}* (`{tx.dest_account}`)\n"
            f"• *Amount*       : `{tx.amount:,.2f} {tx.currency}`\n"
            f"• *Triggers*     :\n{reasons_list}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ *Action*: Advise `{tx.dest_bank}` to verify / hold destination account."
        )

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "Markdown"
        }

        try:
            resp = requests.post(url, json=payload, timeout=3.0)
            if resp.status_code == 200:
                logger.info(f"Telegram alert sent for TX {tx.tx_id}")
                return True
            else:
                logger.error(f"Telegram API error {resp.status_code}: {resp.text}")
                return False
        except Exception as e:
            logger.error(f"Failed to dispatch Telegram alert: {e}")
            return False
