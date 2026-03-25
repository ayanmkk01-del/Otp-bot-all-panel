#!/usr/bin/env python3
"""
OTP মনিটর বট – অটো লগইন + শুধু প্রথম OTP ফরওয়ার্ড
----------------------------------------
- অটোমেটিক লগইন (কুকি এক্সপায়ার হলে নিজেই নতুন করে লগইন করে)
- কোন OTP একবার পাঠালে আর পাঠায় না (২৪ ঘণ্টা মেমোরি)
- aiohttp না থাকলে requests ব্যবহার করবে
- ০.৫ সেকেন্ড পর পর API চেক করে
- দেশের ফ্ল্যাগ সহ ফরম্যাট
"""

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta

# ---------- aiohttp ইম্পোর্ট করার চেষ্টা (না থাকলে requests) ----------
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

# ========== কনফিগারেশন – লগইন তথ্য দিয়ে পূরণ করুন ==========
TELEGRAM_BOT_TOKEN = "8362446113:AAGsrg9iZmeByXmFbig2vdKfmDBUpgppIDM"
GROUP_CHAT_ID = "-1001153782407"
TARGET_URL = "https://imssms.org/client/res/data_smscdr.php"
LOGIN_URL = "https://imssms.org/login"
NUMBER_BOT_URL = "https://t.me/Updateotpnew_bot"

# লগইন ক্রেডেনশিয়াল (আপনার একাউন্টের তথ্য দিন)
LOGIN_USERNAME = "mamun1132"      # স্ক্রিনশট থেকে নেওয়া
LOGIN_PASSWORD = "Mamun1132"      # অনুমানিক পাসওয়ার্ড (সঠিকটি দিন)
# =================================================================

# লগিং সেটআপ
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


class OTPMonitorBot:
    """মূল বট ক্লাস – OTP মনিটর ও টেলিগ্রাম ফরওয়ার্ডার (অটো লগইন সহ)"""

    def __init__(self, telegram_token, group_chat_id, login_username, login_password):
        self.telegram_token = telegram_token
        self.group_chat_id = group_chat_id
        self.login_username = login_username
        self.login_password = login_password
        
        # সেশন কুকি (খালি থাকবে, লগইন করলে পূর্ণ হবে)
        self.session_cookie = None
        self.session = None  # aiohttp সেশন (যদি aiohttp থাকে)
        
        # আগে পাঠানো OTP গুলো JSON ফাইলে সেভ থাকে
        self.storage_file = "processed_otps.json"
        self.processed_otps = self._load_processed_otps()

        self.total_otps_sent = 0
        self.last_otp_time = None
        self.is_monitoring = True
        self.login_attempts = 0

        # OTP শনাক্ত করার রেগুলার এক্সপ্রেশন
        patterns = [
            r"\b\d{3}-\d{3}\b",
            r"\b\d{5}\b",
            r"code\s*\d+",
            r"code:\s*\d+",
            r"কোড\s*\d+",
            r"\b\d{6}\b",
            r"\b\d{4}\b",
            r"Your WhatsApp code \d+-\d+",
            r"WhatsApp code \d+-\d+",
            r"Telegram code \d+",
            r"verification code:?\s*\d+",
            r"OTP:?\s*\d+",
        ]
        self.otp_regex = re.compile("|".join(patterns), re.IGNORECASE)

        # HTTP লাইব্রেরি স্ট্যাটাস
        if HAS_AIOHTTP:
            logger.info("✅ aiohttp ব্যবহার করা হচ্ছে (দ্রুত)")
        else:
            logger.warning("⚠️ aiohttp ইনস্টল নেই – requests ব্যবহার হবে (ধীর)")

    # ---------- অটো লগইন ফাংশন ----------
    async def login(self):
        """imssms.org এ লগইন করে সেশন কুকি নেয়"""
        logger.info(f"🔐 লগইন করার চেষ্টা: {self.login_username}")
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Mobile Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://imssms.org",
            "Referer": "https://imssms.org/login",
        }
        
        # লগইন ডাটা (ফর্ম ডাটা)
        login_data = {
            "username": self.login_username,
            "password": self.login_password,
            "submit": "Sign In"
        }
        
        if HAS_AIOHTTP:
            return await self._login_aiohttp(headers, login_data)
        else:
            return await self._login_requests(headers, login_data)
    
    async def _login_aiohttp(self, headers, login_data):
        """aiohttp দিয়ে লগইন"""
        try:
            async with aiohttp.ClientSession() as session:
                # প্রথমে লগইন পেজে GET request (কুকি পেতে)
                async with session.get(LOGIN_URL, headers=headers, ssl=False) as resp:
                    pass
                
                # POST করে লগইন
                async with session.post(LOGIN_URL, data=login_data, headers=headers, ssl=False, allow_redirects=True) as resp:
                    if resp.status == 200:
                        # কুকি বের করা
                        cookies = session.cookie_jar.filter_cookies('https://imssms.org')
                        if 'PHPSESSID' in cookies:
                            self.session_cookie = cookies['PHPSESSID'].value
                            logger.info(f"✅ লগইন সফল! নতুন সেশন কুকি: {self.session_cookie[:20]}...")
                            self.login_attempts = 0
                            return True
                        else:
                            logger.error("❌ লগইন ব্যর্থ: PHPSESSID কুকি পাওয়া যায়নি")
                            return False
                    else:
                        logger.error(f"❌ লগইন ব্যর্থ: HTTP {resp.status}")
                        return False
        except Exception as e:
            logger.error(f"❌ লগইন এরর: {e}")
            return False
    
    async def _login_requests(self, headers, login_data):
        """requests দিয়ে লগইন"""
        def _sync_login():
            try:
                session = requests.Session()
                # প্রথমে GET
                session.get(LOGIN_URL, headers=headers, verify=False)
                # তারপর POST
                response = session.post(LOGIN_URL, data=login_data, headers=headers, verify=False, allow_redirects=True)
                
                if response.status_code == 200:
                    cookies = session.cookies.get_dict()
                    if 'PHPSESSID' in cookies:
                        self.session_cookie = cookies['PHPSESSID']
                        logger.info(f"✅ লগইন সফল! নতুন সেশন কুকি: {self.session_cookie[:20]}...")
                        self.login_attempts = 0
                        return True
                    else:
                        logger.error("❌ লগইন ব্যর্থ: PHPSESSID কুকি পাওয়া যায়নি")
                        return False
                else:
                    logger.error(f"❌ লগইন ব্যর্থ: HTTP {response.status_code}")
                    return False
            except Exception as e:
                logger.error(f"❌ লগইন এরর: {e}")
                return False
        
        return await asyncio.to_thread(_sync_login)
    
    # ---------- সেশন কুকি ভ্যালিড চেক ----------
    async def check_session_valid(self):
        """বর্তমান সেশন কুকি ভ্যালিড কিনা চেক করে"""
        if not self.session_cookie:
            return False
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36",
            "Cookie": f"PHPSESSID={self.session_cookie}",
            "X-Requested-With": "XMLHttpRequest",
        }
        
        current_date = time.strftime("%Y-%m-%d")
        params = {
            "fdate1": f"{current_date} 00:00:00",
            "fdate2": f"{current_date} 23:59:59",
            "sesskey": "Q05RR0FRUUZCVQ==",
            "sEcho": "1",
            "iColumns": "7",
            "iDisplayStart": "0",
            "iDisplayLength": "1",
            "_": str(int(time.time() * 1000)),
        }
        
        if HAS_AIOHTTP:
            return await self._check_session_aiohttp(headers, params)
        else:
            return await self._check_session_requests(headers, params)
    
    async def _check_session_aiohttp(self, headers, params):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(TARGET_URL, headers=headers, params=params, timeout=5, ssl=False) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        if 'aaData' in text or '{"aaData":' in text:
                            return True
                    return False
        except:
            return False
    
    async def _check_session_requests(self, headers, params):
        def _sync_check():
            try:
                response = requests.get(TARGET_URL, headers=headers, params=params, timeout=5, verify=False)
                if response.status_code == 200:
                    return True
                return False
            except:
                return False
        return await asyncio.to_thread(_sync_check)
    
    # ---------- JSON ফাইল থেকে OTP ID লোড/সেভ ----------
    def _load_processed_otps(self):
        try:
            with open(self.storage_file, "r") as f:
                data = json.load(f)
            cutoff = datetime.now() - timedelta(hours=24)
            valid = {
                otp_id for otp_id, ts in data.items()
                if datetime.fromisoformat(ts) > cutoff
            }
            logger.info(f"📂 {len(valid)} টি OTP ID লোড করা হয়েছে (গত ২৪ ঘণ্টা)")
            return valid
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            return set()

    def _save_processed_otps(self):
        data = {otp_id: datetime.now().isoformat() for otp_id in self.processed_otps}
        with open(self.storage_file, "w") as f:
            json.dump(data, f)

    # ---------- ফরম্যাটিং হেলপার ----------
    @staticmethod
    def hide_phone_number(phone_number):
        if not phone_number:
            return "***"
        phone_str = str(phone_number)
        if len(phone_str) >= 8:
            return phone_str[:4] + "****" + phone_str[-4:]
        elif len(phone_str) >= 4:
            return phone_str[:2] + "***" + phone_str[-2:]
        return "***" + phone_str[-1:] if phone_str else ""

    def extract_country_name(self, operator):
        if not operator:
            return "N/A"
        if '_' in operator:
            return operator.split('_')[0].strip()
        else:
            return operator.split()[0].strip()

    def get_country_flag(self, country_name):
        flags = {
            "Venezuela": "🇻🇪", "Algeria": "🇩🇿", "Honduras": "🇭🇳",
            "Saudi": "🇸🇦", "Saudi Arabia": "🇸🇦", "Pakistan": "🇵🇰",
            "Bangladesh": "🇧🇩", "India": "🇮🇳", "USA": "🇺🇸",
            "UK": "🇬🇧", "UAE": "🇦🇪", "Egypt": "🇪🇬", "Turkey": "🇹🇷",
            "Morocco": "🇲🇦", "Tunisia": "🇹🇳", "Libya": "🇱🇾", "Jordan": "🇯🇴",
            "Kuwait": "🇰🇼", "Oman": "🇴🇲", "Qatar": "🇶🇦", "Bahrain": "🇧🇭",
            "Yemen": "🇾🇪", "Syria": "🇸🇾", "Lebanon": "🇱🇧", "Palestine": "🇵🇸",
            "Iraq": "🇮🇶", "Iran": "🇮🇷", "Afghanistan": "🇦🇫", "Russia": "🇷🇺",
            "China": "🇨🇳", "Malaysia": "🇲🇾", "Indonesia": "🇮🇩", "Thailand": "🇹🇭",
            "Vietnam": "🇻🇳", "Philippines": "🇵🇭", "South Africa": "🇿🇦",
            "Nigeria": "🇳🇬", "Kenya": "🇰🇪", "Ghana": "🇬🇭", "Brazil": "🇧🇷",
            "Argentina": "🇦🇷", "Mexico": "🇲🇽", "Colombia": "🇨🇴", "Chile": "🇨🇱",
            "Peru": "🇵🇪", "Spain": "🇪🇸", "France": "🇫🇷", "Germany": "🇩🇪",
            "Italy": "🇮🇹", "Netherlands": "🇳🇱", "Belgium": "🇧🇪", "Sweden": "🇸🇪",
            "Norway": "🇳🇴", "Denmark": "🇩🇰", "Finland": "🇫🇮", "Poland": "🇵🇱",
            "Czech Republic": "🇨🇿", "Austria": "🇦🇹", "Switzerland": "🇨🇭",
            "Greece": "🇬🇷", "Portugal": "🇵🇹", "Ireland": "🇮🇪", "Australia": "🇦🇺",
            "New Zealand": "🇳🇿", "Canada": "🇨🇦",
        }
        if country_name in flags:
            return flags[country_name]
        first_word = country_name.split()[0]
        for full_name, flag in flags.items():
            if full_name.startswith(first_word):
                return flag
        return ""

    def extract_otp(self, message):
        if not message:
            return None
        match = self.otp_regex.search(message)
        return match.group(0) if match else None

    def create_otp_id(self, timestamp, phone_number, message):
        otp = self.extract_otp(message) or message[:20] if message else "unknown"
        return f"{timestamp}_{phone_number}_{otp}"

    # ---------- টেলিগ্রাম মেসেজ পাঠানো ----------
    async def send_telegram_message(self, message, chat_id=None, reply_markup=None):
        chat_id = chat_id or self.group_chat_id
        try:
            bot = Bot(token=self.telegram_token)
            await bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode="Markdown",
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
            return True
        except TelegramError as e:
            logger.error(f"❌ টেলিগ্রাম এরর: {e}")
            return False

    async def send_startup_message(self):
        startup_msg = f"""
🚀 **OTP মনিটর বট চালু হয়েছে (অটো লগইন সহ)** 🚀
➖➖➖➖➖➖➖➖➖➖➖

✅ **স্ট্যাটাস:** `লাইভ ও মনিটরিং`
🔐 **অটো লগইন:** `সক্রিয়`
⚡ **রেসপন্স:** `তাৎক্ষণিক`

⏰ **চালুর সময়:** `{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}`

🔔 **নোট:** একই OTP একবারই পাঠানো হবে!

➖➖➖➖➖➖➖➖➖➖➖
🤖 **OTP মনিটর বট**
        """
        keyboard = [
            [
                InlineKeyboardButton("👥 Bot Developer", url="https://t.me/rana1132"),
                InlineKeyboardButton("🤖 Number Bot", url=NUMBER_BOT_URL),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await self.send_telegram_message(startup_msg, reply_markup=reply_markup)

    @staticmethod
    def create_response_buttons():
        keyboard = [
            [
                InlineKeyboardButton("👥 Bot Developer", url="https://t.me/rana1132"),
                InlineKeyboardButton("🤖 Number Bot", url=NUMBER_BOT_URL),
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    def format_message(self, sms_data):
        if len(sms_data) < 6:
            return "⚠️ অসম্পূর্ণ SMS ডেটা পাওয়া গেছে"
        
        timestamp = sms_data[0]
        operator = sms_data[1]
        phone_number = sms_data[2]
        platform = sms_data[3]
        message = sms_data[5]

        hidden_phone = self.hide_phone_number(phone_number)
        country = self.extract_country_name(operator)
        flag = self.get_country_flag(country)
        otp_code = self.extract_otp(message) or "প্রসেসিং..."

        return f"""
{flag} {country} #{platform}
{hidden_phone}
{message}

{otp_code}
"""

    # ---------- API থেকে SMS ডেটা ফেচ (অটো রিলগইন সহ) ----------
    async def fetch_sms_data_with_auth(self):
        """সেশন চেক করে, প্রয়োজন হলে লগইন করে, তারপর ডাটা ফেচ করে"""
        # সেশন ভ্যালিড কিনা চেক করুন
        if not self.session_cookie:
            logger.info("কুকি নেই, লগইন করা হচ্ছে...")
            if not await self.login():
                logger.error("লগইন ব্যর্থ, আবার চেষ্টা করা হবে...")
                return None
        
        # সেশন চেক করুন
        if not await self.check_session_valid():
            logger.warning("সেশন এক্সপায়ার হয়েছে, পুনরায় লগইন করা হচ্ছে...")
            if not await self.login():
                logger.error("পুনরায় লগইন ব্যর্থ!")
                return None
        
        # ডাটা ফেচ করুন
        return await self.fetch_sms_data()
    
    async def fetch_sms_data(self):
        """সেশন কুকি দিয়ে API থেকে SMS ডেটা নিয়ে আসো"""
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Mobile Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "en-AZ,en;q=0.9",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://imssms.org/client/SMSCDRStats",
            "Cookie": f"PHPSESSID={self.session_cookie}",
            "Connection": "keep-alive",
            "DNT": "1",
        }
        current_date = time.strftime("%Y-%m-%d")
        params = {
            "fdate1": f"{current_date} 00:00:00",
            "fdate2": f"{current_date} 23:59:59",
            "frange": "",
            "fnum": "",
            "fcli": "",
            "fgdate": "",
            "fgmonth": "",
            "fgrange": "",
            "fgnumber": "",
            "fgcli": "",
            "fg": "0",
            "sesskey": "Q05RR0FRUUZCVQ==",
            "sEcho": "1",
            "iColumns": "7",
            "sColumns": ",,,,,,,",
            "iDisplayStart": "0",
            "iDisplayLength": "25",
            "mDataProp_0": "0",
            "sSearch_0": "",
            "bRegex_0": "false",
            "bSearchable_0": "true",
            "bSortable_0": "true",
            "mDataProp_1": "1",
            "sSearch_1": "",
            "bRegex_1": "false",
            "bSearchable_1": "true",
            "bSortable_1": "true",
            "mDataProp_2": "2",
            "sSearch_2": "",
            "bRegex_2": "false",
            "bSearchable_2": "true",
            "bSortable_2": "true",
            "mDataProp_3": "3",
            "sSearch_3": "",
            "bRegex_3": "false",
            "bSearchable_3": "true",
            "bSortable_3": "true",
            "mDataProp_4": "4",
            "sSearch_4": "",
            "bRegex_4": "false",
            "bSearchable_4": "true",
            "bSortable_4": "true",
            "mDataProp_5": "5",
            "sSearch_5": "",
            "bRegex_5": "false",
            "bSearchable_5": "true",
            "bSortable_5": "true",
            "mDataProp_6": "6",
            "sSearch_6": "",
            "bRegex_6": "false",
            "bSearchable_6": "true",
            "bSortable_6": "true",
            "sSearch": "",
            "bRegex": "false",
            "iSortCol_0": "0",
            "sSortDir_0": "desc",
            "iSortingCols": "1",
            "_": str(int(time.time() * 1000)),
        }

        if HAS_AIOHTTP:
            return await self._fetch_aiohttp(headers, params)
        else:
            return await self._fetch_requests(headers, params)

    async def _fetch_aiohttp(self, headers, params):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.target_url,
                    headers=headers,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=10),
                    ssl=False
                ) as response:
                    if response.status == 200:
                        text = await response.text()
                        if text and text.strip():
                            return json.loads(text)
                    return None
        except Exception as e:
            logger.warning(f"⚠️ aiohttp ফেচ এরর: {e}")
            return None

    async def _fetch_requests(self, headers, params):
        def _sync_fetch():
            try:
                response = requests.get(
                    self.target_url,
                    headers=headers,
                    params=params,
                    timeout=10,
                    verify=False
                )
                if response.status_code == 200 and response.text and response.text.strip():
                    return response.json()
            except Exception as e:
                logger.warning(f"⚠️ requests ফেচ এরর: {e}")
            return None
        return await asyncio.to_thread(_sync_fetch)

    # ---------- মূল মনিটর লুপ ----------
    async def monitor_loop(self):
        logger.info("🚀 OTP মনিটরিং শুরু – অটো লগইন সক্রিয়")
        await self.send_startup_message()

        consecutive_failures = 0
        retry_delay = 0.5

        while self.is_monitoring:
            try:
                data = await self.fetch_sms_data_with_auth()

                if data and "aaData" in data:
                    consecutive_failures = 0
                    retry_delay = 0.5

                    sms_list = data["aaData"]
                    valid_sms = [
                        sms for sms in sms_list
                        if len(sms) >= 6 and isinstance(sms[0], str) and ":" in sms[0]
                    ]

                    if valid_sms:
                        valid_sms.reverse()

                        for sms in valid_sms:
                            timestamp = sms[0]
                            phone = sms[2]
                            message = sms[5] if len(sms) > 5 else ""
                            otp_id = self.create_otp_id(timestamp, phone, message)

                            if otp_id not in self.processed_otps:
                                logger.info(f"🚨 নতুন OTP ডিটেক্ট: {timestamp} - {phone}")

                                formatted_msg = self.format_message(sms)
                                reply_markup = self.create_response_buttons()
                                success = await self.send_telegram_message(
                                    formatted_msg, reply_markup=reply_markup
                                )

                                if success:
                                    self.processed_otps.add(otp_id)
                                    self.total_otps_sent += 1
                                    self.last_otp_time = datetime.now().strftime("%H:%M:%S")
                                    logger.info(f"✅ OTP পাঠানো হয়েছে (#{self.total_otps_sent})")
                                    self._save_processed_otps()
                                else:
                                    logger.error(f"❌ OTP পাঠানো ব্যর্থ: {otp_id}")
                                break
                    else:
                        logger.debug("ℹ️ কোনো বৈধ SMS পাওয়া যায়নি")
                else:
                    consecutive_failures += 1
                    retry_delay = min(retry_delay * 1.5, 5.0)
                    logger.warning(f"⚠️ API এরর। {retry_delay:.1f} সেকেন্ড পর আবার চেষ্টা")

                await asyncio.sleep(retry_delay if consecutive_failures > 0 else 0.5)

            except asyncio.CancelledError:
                logger.info("🛑 মনিটর লুপ বন্ধ করা হয়েছে")
                break
            except Exception as e:
                logger.exception(f"❌ অপ্রত্যাশিত এরর: {e}")
                await asyncio.sleep(1)


async def main():
    print("=" * 50)
    print("🤖 OTP মনিটর বট – অটো লগইন সহ")
    print("=" * 50)
    print(f"⚡ মোড: প্রথম OTP + অটো রিলগইন")
    print(f"🔐 লগইন ইউজার: {LOGIN_USERNAME}")
    print(f"📱 গ্রুপ আইডি: {GROUP_CHAT_ID}")
    print("🚀 বট চালু হচ্ছে...")
    print("=" * 50)

    bot = OTPMonitorBot(
        telegram_token=TELEGRAM_BOT_TOKEN,
        group_chat_id=GROUP_CHAT_ID,
        login_username=LOGIN_USERNAME,
        login_password=LOGIN_PASSWORD,
    )
    
    # target_url সেট করুন
    bot.target_url = TARGET_URL

    try:
        await bot.monitor_loop()
    except KeyboardInterrupt:
        print("\n🛑 ব্যবহারকারী বট বন্ধ করেছেন!")
        bot.is_monitoring = False
        print(f"📊 সর্বমোট OTP পাঠানো: {bot.total_otps_sent}")
        print("👋 আল্লাহ হাফেজ!")


if __name__ == "__main__":
    asyncio.run(main())