#!/usr/bin/env python3
"""
OTP Monitor Bot - Railway Deployment (No Persistent Volume)
"""

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import Dict

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

# ========== CONFIGURATION ==========
TELEGRAM_BOT_TOKEN = "5929619535:AAGsgoN5pYczsKWOGqVWTrslk0qJr2jJVYA"
GROUP_CHAT_ID = "-1001153782407"
TARGET_URL = "http://147.135.212.148/ints/agent/res/data_smscdr.php"
LOGIN_URL = "http://147.135.212.148/ints/agent/SMSCDRStats"
NUMBER_BOT_URL = "https://t.me/Updateotpnew_bot"
DEVELOPER_URL = "https://t.me/rana1132"
SESSKEY = "Q05RR0FRUURCUA=="

# Use temporary directory for files (will be lost on restart)
import tempfile
TEMP_DIR = tempfile.gettempdir()
COOKIE_FILE = os.path.join(TEMP_DIR, "session_cookies.json")
OTP_FILE = os.path.join(TEMP_DIR, "processed_otps.json")
# ====================================

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


class CookieManager:
    def __init__(self):
        self.cookies: Dict[str, str] = {}
        self.last_refresh = None
        self.load()
        
    def load(self):
        try:
            if os.path.exists(COOKIE_FILE):
                with open(COOKIE_FILE, 'r') as f:
                    data = json.load(f)
                    self.cookies = data.get('cookies', {})
                    self.last_refresh = datetime.fromisoformat(data.get('last_refresh', '2000-01-01'))
                    logger.info(f"â Loaded {len(self.cookies)} cookies")
            else:
                logger.info("No existing cookies found")
        except Exception as e:
            logger.error(f"Cookie load error: {e}")
    
    def save(self):
        try:
            data = {
                'cookies': self.cookies,
                'last_refresh': datetime.now().isoformat()
            }
            with open(COOKIE_FILE, 'w') as f:
                json.dump(data, f)
            logger.debug("Cookies saved")
        except Exception as e:
            logger.error(f"Cookie save error: {e}")
    
    def get_string(self) -> str:
        if not self.cookies:
            return ""
        return "; ".join([f"{k}={v}" for k, v in self.cookies.items()])
    
    async def refresh(self, session=None):
        logger.info("ð Refreshing cookies...")
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 13; Mobile; rv:128.0) Gecko/128.0 Firefox/128.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
            "X-Requested-With": "XMLHttpRequest",
            "Sec-GPC": "1",
            "Connection": "keep-alive",
        }
        try:
            if HAS_AIOHTTP and session:
                async with session.get(LOGIN_URL, headers=headers, ssl=False) as resp:
                    if resp.status == 200:
                        self.cookies = {k: v.value for k, v in resp.cookies.items()}
                        self.last_refresh = datetime.now()
                        self.save()
                        logger.info("â Cookies refreshed successfully")
                        return True
            else:
                resp = requests.get(LOGIN_URL, headers=headers, verify=False, timeout=10)
                if resp.status_code == 200:
                    self.cookies = dict(resp.cookies)
                    self.last_refresh = datetime.now()
                    self.save()
                    logger.info("â Cookies refreshed successfully")
                    return True
        except Exception as e:
            logger.error(f"Cookie refresh error: {e}")
        return False
    
    def is_expired(self):
        if not self.cookies:
            return True
        if not self.last_refresh:
            return True
        return (datetime.now() - self.last_refresh).total_seconds() > 3600
    
    async def ensure(self, session=None):
        if self.is_expired():
            logger.info("Cookies expired, refreshing...")
            return await self.refresh(session)
        return True


class OTPBot:
    def __init__(self):
        self.token = TELEGRAM_BOT_TOKEN
        self.chat_id = GROUP_CHAT_ID
        self.cookies = CookieManager()
        self.processed = self.load_processed()
        self.total_sent = 0
        
        # OTP patterns
        patterns = [
            r"\b\d{3}-\d{3}\b", r"\b\d{5}\b", r"\b\d{6}\b", r"\b\d{4}\b",
            r"code\s*:?\s*\d+", r"OTP:?\s*\d+", r"verification code:?\s*\d+",
            r"Your WhatsApp code \d+-\d+", r"Telegram code \d+",
            r"à¦à§à¦¡\s*\d+", r"verification:\s*\d+"
        ]
        self.otp_regex = re.compile("|".join(patterns), re.IGNORECASE)
    
    def load_processed(self):
        try:
            if os.path.exists(OTP_FILE):
                with open(OTP_FILE, 'r') as f:
                    data = json.load(f)
                cutoff = datetime.now() - timedelta(hours=24)
                valid = {k for k, v in data.items() if datetime.fromisoformat(v) > cutoff}
                logger.info(f"ð Loaded {len(valid)} OTPs from last 24 hours")
                return valid
        except Exception as e:
            logger.error(f"Load OTP error: {e}")
        return set()
    
    def save_processed(self):
        try:
            data = {k: datetime.now().isoformat() for k in self.processed}
            with open(OTP_FILE, 'w') as f:
                json.dump(data, f)
            logger.debug(f"ð¾ Saved {len(self.processed)} OTPs")
        except Exception as e:
            logger.error(f"Save OTP error: {e}")
    
    def hide_phone(self, phone):
        if not phone:
            return "***"
        p = str(phone)
        if len(p) >= 8:
            return p[:4] + "****" + p[-4:]
        elif len(p) >= 4:
            return p[:2] + "***" + p[-2:]
        return p
    
    def get_flag(self, country):
        flags = {
            "Bangladesh": "ð§ð©", "India": "ð®ð³", "Pakistan": "ðµð°",
            "Saudi": "ð¸ð¦", "UAE": "ð¦ðª", "USA": "ðºð¸", "UK": "ð¬ð§",
            "Turkey": "ð¹ð·", "Egypt": "ðªð¬", "Malaysia": "ð²ð¾",
            "Indonesia": "ð®ð©", "Thailand": "ð¹ð­", "Vietnam": "ð»ð³",
            "Philippines": "ðµð­", "Brazil": "ð§ð·", "Argentina": "ð¦ð·",
            "Mexico": "ð²ð½", "Spain": "ðªð¸", "France": "ð«ð·",
            "Germany": "ð©ðª", "Italy": "ð®ð¹", "Netherlands": "ð³ð±",
            "Venezuela": "ð»ðª", "Algeria": "ð©ð¿", "Honduras": "ð­ð³",
            "Morocco": "ð²ð¦", "Tunisia": "ð¹ð³", "Libya": "ð±ð¾",
            "Jordan": "ð¯ð´", "Kuwait": "ð°ð¼", "Oman": "ð´ð²",
            "Qatar": "ð¶ð¦", "Bahrain": "ð§ð­", "Iran": "ð®ð·",
            "Iraq": "ð®ð¶", "Afghanistan": "ð¦ð«", "Russia": "ð·ðº",
            "China": "ð¨ð³", "South Africa": "ð¿ð¦", "Nigeria": "ð³ð¬"
        }
        for k, v in flags.items():
            if k in country:
                return v
        return "ð"
    
    def extract_otp(self, message):
        if not message:
            return None
        match = self.otp_regex.search(message)
        return match.group(0) if match else None
    
    def format_msg(self, sms):
        if len(sms) < 6:
            return "â ï¸ Invalid SMS data"
        
        timestamp = sms[0] if len(sms) > 0 else "N/A"
        operator = sms[1] if len(sms) > 1 else "N/A"
        phone = sms[2] if len(sms) > 2 else "N/A"
        platform = sms[3] if len(sms) > 3 else "N/A"
        message = sms[5] if len(sms) > 5 else "N/A"
        
        country = operator.split('_')[0] if '_' in operator else operator.split()[0]
        flag = self.get_flag(country)
        hidden = self.hide_phone(phone)
        
        otp = self.extract_otp(message) or "???"
        
        try:
            time_str = timestamp.split()[1] if ' ' in timestamp else timestamp[:8]
        except:
            time_str = timestamp
        
        return f"""
{flag} **{country}** #{platform}
ð± `{hidden}`
â° {time_str}

ð¨ {message}

ð **OTP:** `{otp}`

ââââââââ
ð¤ @OTPMonitorBot
"""
    
    async def send(self, text):
        try:
            bot = Bot(token=self.token)
            keyboard = [[
                InlineKeyboardButton("ð¥ Developer", url=DEVELOPER_URL),
                InlineKeyboardButton("ð¤ Number Bot", url=NUMBER_BOT_URL),
            ]]
            await bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard),
                disable_web_page_preview=True
            )
            return True
        except TelegramError as e:
            logger.error(f"Telegram error: {e}")
            return False
        except Exception as e:
            logger.error(f"Send error: {e}")
            return False
    
    async def fetch(self):
        if not await self.cookies.ensure():
            return None
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Android 13; Mobile; rv:128.0) Gecko/128.0 Firefox/128.0",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "en-AZ,it-SI;q=0.8,es-BO;q=0.5,ar-IL;q=0.3",
            "X-Requested-With": "XMLHttpRequest",
            "Sec-GPC": "1",
            "Referer": LOGIN_URL,
            "Cookie": self.cookies.get_string(),
            "Connection": "keep-alive",
        }
        
        today = time.strftime("%Y-%m-%d")
        params = {
            "fdate1": f"{today} 00:00:00",
            "fdate2": f"{today} 23:59:59",
            "frange": "", "fclient": "", "fnum": "", "fcli": "",
            "fgdate": "", "fgmonth": "", "fgrange": "", "fgclient": "",
            "fgnumber": "", "fgcli": "", "fg": "0",
            "sesskey": SESSKEY,
            "sEcho": "1", "iColumns": "9", "sColumns": ",,,,,,,,",
            "iDisplayStart": "0", "iDisplayLength": "25",
            "sSearch": "", "bRegex": "false",
            "iSortCol_0": "0", "sSortDir_0": "desc",
            "iSortingCols": "1", "_": str(int(time.time() * 1000)),
        }
        
        for i in range(9):
            params[f"mDataProp_{i}"] = str(i)
            params[f"sSearch_{i}"] = ""
            params[f"bRegex_{i}"] = "false"
            params[f"bSearchable_{i}"] = "true"
            params[f"bSortable_{i}"] = "true" if i != 8 else "false"
        
        try:
            if HAS_AIOHTTP:
                async with aiohttp.ClientSession() as session:
                    async with session.get(TARGET_URL, headers=headers, params=params, timeout=15, ssl=False) as resp:
                        if resp.status == 200:
                            text = await resp.text()
                            if text and text.strip():
                                if resp.cookies:
                                    for cookie in resp.cookies.values():
                                        self.cookies.cookies[cookie.key] = cookie.value
                                    self.cookies.save()
                                return json.loads(text)
                        elif resp.status in [403, 401]:
                            logger.warning("Auth error, refreshing cookies...")
                            await self.cookies.refresh(session)
                            return None
                        else:
                            logger.warning(f"HTTP {resp.status}")
            else:
                resp = requests.get(TARGET_URL, headers=headers, params=params, timeout=15, verify=False)
                if resp.status_code == 200:
                    if resp.cookies:
                        for key, value in resp.cookies.items():
                            self.cookies.cookies[key] = value
                        self.cookies.save()
                    return resp.json()
                elif resp.status_code in [403, 401]:
                    logger.warning("Auth error, refreshing cookies...")
                    await self.cookies.refresh()
        except asyncio.TimeoutError:
            logger.warning("Request timeout")
        except Exception as e:
            logger.error(f"Fetch error: {e}")
        return None
    
    async def run(self):
        logger.info("=" * 50)
        logger.info("ð OTP Monitor Bot Started on Railway!")
        logger.info("=" * 50)
        
        # Send startup message
        startup_msg = f"""
ð **OTP Monitor Bot LIVE on Railway** ð
â° **Started:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
â **Status:** `Active`
ð **Cookie Refresh:** Auto (every 1 hour)
ð¡ **API:** Connected

**Features:**
â¢ Real-time OTP monitoring
â¢ First OTP only forwarding
â¢ 24-hour duplicate prevention
â¢ Auto cookie refresh

â ï¸ **Note:** Data resets on restart (no persistent storage)

ââââââââ
ð¤ **Waiting for OTPs...**
"""
        await self.send(startup_msg)
        logger.info("â Startup message sent to Telegram")
        
        while True:
            try:
                data = await self.fetch()
                
                if data and "aaData" in data:
                    sms_list = data["aaData"]
                    valid_sms = [s for s in sms_list if len(s) >= 6 and isinstance(s[0], str) and ":" in s[0]]
                    
                    if valid_sms:
                        valid_sms.reverse()
                        
                        for sms in valid_sms:
                            timestamp = sms[0] if len(sms) > 0 else ""
                            phone = sms[2] if len(sms) > 2 else ""
                            message = sms[5] if len(sms) > 5 else ""
                            otp = self.extract_otp(message) or ""
                            otp_id = f"{timestamp}_{phone}_{otp}"
                            
                            if otp_id not in self.processed:
                                logger.info(f"ð¨ New OTP detected! Phone: {phone}")
                                formatted_msg = self.format_msg(sms)
                                
                                if await self.send(formatted_msg):
                                    self.processed.add(otp_id)
                                    self.total_sent += 1
                                    self.save_processed()
                                    logger.info(f"â OTP forwarded! Total: {self.total_sent}")
                                else:
                                    logger.error(f"â Failed to send OTP")
                                break
                else:
                    logger.debug("No new SMS data")
                
                await asyncio.sleep(1)
                
            except asyncio.CancelledError:
                logger.info("Bot stopped")
                break
            except Exception as e:
                logger.exception(f"Loop error: {e}")
                await asyncio.sleep(5)


async def main():
    bot = OTPBot()
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())