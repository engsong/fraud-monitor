import json
import re
from datetime import datetime, timezone
from typing import Optional
from lxml import etree
from src.common.models import Transaction
from src.common.logger import get_logger

logger = get_logger("Parser")

class TransactionParser:
    """
    Parses LMPS Switching logs, ISO 20022 XML messages, and REST API JSON payloads.
    """

    @staticmethod
    def parse(raw_data: str) -> Optional[Transaction]:
        cleaned = raw_data.strip()
        if not cleaned:
            return None

        # 1. LMPS Switching Core Log lines (e.g. from LMPS.log)
        # Exclusively match inbound [POST /transfer] to prevent duplicate counting of forwarded HTTP requests
        if "[POST /transfer]" in cleaned:
            return TransactionParser._parse_lmps_log(cleaned)

        # 2. Standard JSON Payload
        elif cleaned.startswith("{") or cleaned.startswith("["):
            return TransactionParser._parse_json(cleaned)

        # 3. ISO 20022 XML Message
        elif cleaned.startswith("<"):
            return TransactionParser._parse_iso20022_xml(cleaned)

        else:
            return None

    @staticmethod
    def _parse_lmps_log(line: str) -> Optional[Transaction]:
        """
        Parses real-time LMPS payment switch inbound transfer log line:
        Example: ... [POST /transfer]	[{"frommember":"LFX","fromaccount":"23000...","tomember":"BCEL","toaccount":"0401...","reference":"BWRY...","amount":10000.00,"ccy":"LAK",...}]
        """
        try:
            match = re.search(r'\[POST /transfer\]\s*\[(\{.*?\})\]', line)
            if not match:
                return None

            json_text = match.group(1)

            data = json.loads(json_text)

            tx_id = str(data.get("reference") or data.get("id") or "")
            source_bank = str(data.get("frommember") or "UNKNOWN")
            source_account = str(data.get("fromaccount") or data.get("fromuser") or "")
            dest_bank = str(data.get("tomember") or "UNKNOWN")
            dest_account = str(data.get("toaccount") or "")
            amount = float(data.get("amount", 0.0))
            currency = str(data.get("ccy") or "LAK")
            channel = str(data.get("totype") or "LMPS_TRANSFER")

            # Parse timestamp from log line or payload
            time_str = data.get("time")
            if time_str:
                try:
                    ts = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                except Exception:
                    ts = datetime.now(timezone.utc)
            else:
                ts = datetime.now(timezone.utc)

            return Transaction(
                tx_id=tx_id,
                timestamp=ts,
                channel=channel,
                source_bank=source_bank,
                source_account=source_account,
                dest_bank=dest_bank,
                dest_account=dest_account,
                amount=amount,
                currency=currency,
                status="SUCCESS",
                response_code="00"
            )
        except Exception as e:
            logger.debug(f"Could not parse LMPS log line: {e}")
            return None

    @staticmethod
    def _parse_json(json_str: str) -> Optional[Transaction]:
        try:
            data = json.loads(json_str)
            if isinstance(data, list) and len(data) > 0:
                data = data[0]
            payload = data.get("data", data)

            tx_id = str(payload.get("tx_id") or payload.get("txn_id") or payload.get("reference") or "")
            source_bank = str(payload.get("source_bank") or payload.get("frommember") or "UNKNOWN")
            source_account = str(payload.get("source_account") or payload.get("fromaccount") or "")
            dest_bank = str(payload.get("dest_bank") or payload.get("tomember") or "UNKNOWN")
            dest_account = str(payload.get("dest_account") or payload.get("toaccount") or "")
            amount = float(payload.get("amount", 0.0))
            currency = str(payload.get("currency") or payload.get("ccy") or "LAK")
            channel = str(payload.get("channel") or payload.get("totype") or "REST_API")

            return Transaction(
                tx_id=tx_id,
                timestamp=datetime.now(timezone.utc),
                channel=channel,
                source_bank=source_bank,
                source_account=source_account,
                dest_bank=dest_bank,
                dest_account=dest_account,
                amount=amount,
                currency=currency,
                status="SUCCESS",
                response_code="00"
            )
        except Exception as e:
            logger.debug(f"Failed to parse JSON: {e}")
            return None

    @staticmethod
    def _parse_iso20022_xml(xml_str: str) -> Optional[Transaction]:
        try:
            root = etree.fromstring(xml_str.encode('utf-8'))
            for elem in root.iter():
                if "}" in elem.tag:
                    elem.tag = elem.tag.split("}", 1)[1]

            msg_id = root.findtext(".//GrpHdr/MsgId") or root.findtext(".//MsgId") or ""
            tx_id = root.findtext(".//PmtId/EndToEndId") or root.findtext(".//PmtId/TxId") or msg_id

            amt_elem = root.find(".//IntrBkSttlmAmt")
            if amt_elem is None:
                amt_elem = root.find(".//InstdAmt")

            amount = float(amt_elem.text) if amt_elem is not None and amt_elem.text else 0.0
            currency = amt_elem.get("Ccy", "LAK") if amt_elem is not None else "LAK"

            source_bank = root.findtext(".//DbtrAgt//BICFI") or root.findtext(".//DbtrAgt//Id") or "UNKNOWN_BANK"
            source_account = root.findtext(".//DbtrAcct//Othr/Id") or root.findtext(".//DbtrAcct//IBAN") or ""
            dest_bank = root.findtext(".//CdtrAgt//BICFI") or root.findtext(".//CdtrAgt//Id") or "UNKNOWN_BANK"
            dest_account = root.findtext(".//CdtrAcct//Othr/Id") or root.findtext(".//CdtrAcct//IBAN") or ""

            return Transaction(
                tx_id=tx_id,
                timestamp=datetime.now(timezone.utc),
                channel="ISO20022",
                source_bank=source_bank,
                source_account=source_account,
                dest_bank=dest_bank,
                dest_account=dest_account,
                amount=amount,
                currency=currency,
                status="SUCCESS",
                response_code="00"
            )
        except Exception as e:
            logger.debug(f"Failed to parse ISO XML: {e}")
            return None
