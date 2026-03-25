#!/usr/bin/env python3
"""
OTP মনিটর বট – শুধু প্রথম OTP ফরওয়ার্ড করে
----------------------------------------
- কোন OTP একবার পাঠালে আর পাঠায় না (২৪ ঘণ্টা মেমোরি)
- aiohttp না থাকলে requests ব্যবহার করবে (যেকোনো পরিবেশে চলে)
- ০.৫ সেকেন্ড পর পর API চেক করে
- এরর লগ ও রিট্রাই সহ
- দেশের ফ্ল্যাগ সহ ফরম্যাট (ছবির মতো)
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

# ========== কনফিগারেশন – আপডেটেড তথ্য ==========
TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    "5929619535:AAGsgoN5pYczsKWOGqVWTrslk0qJr2jJVYA"
)
GROUP_CHAT_ID = os.getenv(
    "GROUP_CHAT_ID",
    "-1001153782407"
)
SESSION_COOKIE = os.getenv(
    "SESSION_COOKIE",
    "bd10e8dc1199015c0372e62784b173f3"  # আপডেটেড কুকিজ
)
TARGET_URL = os.getenv(
    "TARGET_URL",
    "http://147.135.212.148/ints/agent/res/data_smscdr.php"  # আপডেটেড URL
)
NUMBER_BOT_URL = os.getenv(
    "NUMBER_BOT_URL",
    "https://t.me/Updateotpnew_bot"
)
# =================================================================

# লগিং সেটআপ
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


class OTPMonitorBot:
    """মূল বট ক্লাস – OTP মনিটর ও টেলিগ্রাম ফরওয়ার্ডার"""

    def __init__(self, telegram_token, group_chat_id, session_cookie, target_url):
        self.telegram_token = telegram_token
        self.group_chat_id = group_chat_id
        self.session_cookie = session_cookie
        self.target_url = target_url

        # আগে পাঠানো OTP গুলো JSON ফাইলে সেভ থাকে (রিস্টার্ট করেও ডুপ্লিকেট রোধ)
        self.storage_file = "processed_otps.json"
        self.processed_otps = self._load_processed_otps()

        self.total_otps_sent = 0
        self.last_otp_time = None
        self.is_monitoring = True

        # OTP শনাক্ত করার রেগুলার এক্সপ্রেশন (বাংলা + ইংরেজি)
        patterns = [
            r"\b\d{3}-\d{3}\b",          # 123-456
            r"\b\d{5}\b",                # 5 ডিজিট
            r"code\s*\d+",              # code 12345
            r"code:\s*\d+",             # code: 12345
            r"কোড\s*\d+",               # কোড 12345
            r"\b\d{6}\b",               # 6 ডিজিট
            r"\b\d{4}\b",               # 4 ডিজিট
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
            logger.warning("⚠️ aiohttp ইনস্টল নেই – requests ব্যবহার হবে (ধীর). 'pip install aiohttp' দিন ভালো পারফরম্যান্সের জন্য")

    # ---------- JSON ফাইল থেকে OTP ID লোড/সেভ ----------
    def _load_processed_otps(self):
        """JSON ফাইল থেকে প্রসেসড OTP ID লোড করে, ২৪ ঘণ্টার পুরোনো ডিলিট করে"""
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
        """বর্তমান OTP ID গুলো JSON ফাইলে সেভ করে"""
        data = {otp_id: datetime.now().isoformat() for otp_id in self.processed_otps}
        with open(self.storage_file, "w") as f:
            json.dump(data, f)
        logger.debug(f"💾 {len(self.processed_otps)} টি OTP ID সেভ করা হয়েছে")

    # ---------- ফরম্যাটিং হেলপার ----------
    @staticmethod
    def hide_phone_number(phone_number):
        """ফোন নাম্বারের মাঝের ডিজিটগুলো লুকাও (যেমন: 01712****34)"""
        if not phone_number:
            return "***"
        phone_str = str(phone_number)
        if len(phone_str) >= 8:
            return phone_str[:4] + "****" + phone_str[-4:]
        elif len(phone_str) >= 4:
            return phone_str[:2] + "***" + phone_str[-2:]
        return "***" + phone_str[-1:] if phone_str else ""

    def extract_country_name(self, operator):
        """অপারেটর স্ট্রিং থেকে শুধু দেশের নাম বের করো (আন্ডারস্কোর বা স্পেসের আগের অংশ)"""
        if not operator:
            return "N/A"
        if '_' in operator:
            return operator.split('_')[0].strip()
        else:
            return operator.split()[0].strip()

    def get_country_flag(self, country_name):
        """দেশের নাম অনুযায়ী ফ্ল্যাগ ইমোজি ফেরত দাও"""
        flags = {
            "Venezuela": "🇻🇪",
            "Algeria": "🇩🇿",
            "Honduras": "🇭🇳",
            "Saudi": "🇸🇦",
            "Saudi Arabia": "🇸🇦",
            "Pakistan": "🇵🇰",
            "Bangladesh": "🇧🇩",
            "India": "🇮🇳",
            "USA": "🇺🇸",
            "UK": "🇬🇧",
            "UAE": "🇦🇪",
            "Egypt": "🇪🇬",
            "Turkey": "🇹🇷",
            "Morocco": "🇲🇦",
            "Tunisia": "🇹🇳",
            "Libya": "🇱🇾",
            "Jordan": "🇯🇴",
            "Kuwait": "🇰🇼",
            "Oman": "🇴🇲",
            "Qatar": "🇶🇦",
            "Bahrain": "🇧🇭",
            "Yemen": "🇾🇪",
            "Syria": "🇸🇾",
            "Lebanon": "🇱🇧",
            "Palestine": "🇵🇸",
            "Iraq": "🇮🇶",
            "Iran": "🇮🇷",
            "Afghanistan": "🇦🇫",
            "Russia": "🇷🇺",
            "China": "🇨🇳",
            "Malaysia": "🇲🇾",
            "Indonesia": "🇮🇩",
            "Thailand": "🇹🇭",
            "Vietnam": "🇻🇳",
            "Philippines": "🇵🇭",
            "South Africa": "🇿🇦",
            "Nigeria": "🇳🇬",
            "Kenya": "🇰🇪",
            "Ghana": "🇬🇭",
            "Brazil": "🇧🇷",
            "Argentina": "🇦🇷",
            "Mexico": "🇲🇽",
            "Colombia": "🇨🇴",
            "Chile": "🇨🇱",
            "Peru": "🇵🇪",
            "Spain": "🇪🇸",
            "France": "🇫🇷",
            "Germany": "🇩🇪",
            "Italy": "🇮🇹",
            "Netherlands": "🇳🇱",
            "Belgium": "🇧🇪",
            "Sweden": "🇸🇪",
            "Norway": "🇳🇴",
            "Denmark": "🇩🇰",
            "Finland": "🇫🇮",
            "Poland": "🇵🇱",
            "Czech Republic": "🇨🇿",
            "Austria": "🇦🇹",
            "Switzerland": "🇨🇭",
            "Greece": "🇬🇷",
            "Portugal": "🇵🇹",
            "Ireland": "🇮🇪",
            "Australia": "🇦🇺",
            "New Zealand": "🇳🇿",
            "Canada": "🇨🇦",
        }
        # পুরো নাম দিয়ে খুঁজি
        if country_name in flags:
            return flags[country_name]
        # যদি না পাই, প্রথম শব্দ দিয়ে চেষ্টা
        first_word = country_name.split()[0]
        for full_name, flag in flags.items():
            if full_name.startswith(first_word):
                return flag
        return ""

    def extract_otp(self, message):
        """মেসেজ থেকে OTP কোড বের করো"""
        if not message:
            return None
        match = self.otp_regex.search(message)
        return match.group(0) if match else None

    def create_otp_id(self, timestamp, phone_number, message):
        """ইউনিক OTP আইডি জেনারেট করো (টাইমস্ট্যাম্প + ফোন + OTP)"""
        otp = self.extract_otp(message) or message[:20] if message else "unknown"
        return f"{timestamp}_{phone_number}_{otp}"

    # ---------- টেলিগ্রাম মেসেজ পাঠানো ----------
    async def send_telegram_message(self, message, chat_id=None, reply_markup=None):
        """টেলিগ্রাম গ্রুপে মেসেজ পাঠাও"""
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
        """বট চালু হওয়ার বার্তা গ্রুপে পাঠাও (শুধু প্রয়োজনীয় বাটন)"""
        startup_msg = f"""
🚀 **OTP মনিটর বট চালু হয়েছে** 🚀
➖➖➖➖➖➖➖➖➖➖➖

✅ **স্ট্যাটাস:** `লাইভ ও মনিটরিং`
⚡ **রেসপন্স:** `তাৎক্ষণিক`
📡 **মোড:** `রিয়েল-টাইম`

🎯 **ফিচার:**
• শুধু প্রথম OTP ফরওয়ার্ড
• লাইভ মনিটরিং
• অটো ডিটেকশন

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
        logger.info("✅ স্টার্টআপ মেসেজ গ্রুপে পাঠানো হয়েছে")

    @staticmethod
    def create_response_buttons():
        """OTP মেসেজের সাথে ইনলাইন বাটন তৈরি করো (ছবির মতো)"""
        keyboard = [
            [
                InlineKeyboardButton("👥 Bot Developer", url="https://t.me/rana1132"),
                InlineKeyboardButton("🤖 Number Bot", url=NUMBER_BOT_URL),
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    def format_message(self, sms_data):
        """SMS ডেটা থেকে টেলিগ্রাম মেসেজ ফরম্যাট করো (ছবির স্টাইলে + ফ্ল্যাগ)"""
        if len(sms_data) < 6:
            logger.warning(f"অসম্পূর্ণ SMS ডেটা: {sms_data}")
            return "⚠️ অসম্পূর্ণ SMS ডেটা পাওয়া গেছে"

        # sms_data: [timestamp, operator, phone, platform, ?, message, ...]
        timestamp = sms_data[0] if len(sms_data) > 0 else "N/A"
        operator = sms_data[1] if len(sms_data) > 1 else "N/A"        # দেশ+অপারেটর
        phone_number = sms_data[2] if len(sms_data) > 2 else "N/A"
        platform = sms_data[3] if len(sms_data) > 3 else "N/A"        # সার্ভিস (Samsung, WA, YallaLudo ইত্যাদি)
        message = sms_data[5] if len(sms_data) > 5 else "N/A"

        hidden_phone = self.hide_phone_number(phone_number)
        country = self.extract_country_name(operator)                 # শুধু দেশের নাম
        flag = self.get_country_flag(country)                         # ফ্ল্যাগ ইমোজি
        otp_code = self.extract_otp(message) or "প্রসেসিং..."

        # ছবির মতো ফরম্যাট (টাইমস্ট্যাম্প বাদ, ফ্ল্যাগ+দেশ)
        return f"""
{flag} {country} #{platform}
{hidden_phone}
{message}

{otp_code}
"""

    # ---------- API থেকে SMS ডেটা ফেচ (aiohttp/requests অটো সিলেক্ট) ----------
    async def fetch_sms_data(self):
        """টার্গেট API থেকে SMS ডেটা নিয়ে আসো"""
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Mobile Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "en-AZ,en;q=0.9,it-SI;q=0.8,it;q=0.7,es-BO;q=0.6,es;q=0.5,ar-IL;q=0.4,ar;q=0.3,en-GB;q=0.2,en-US;q=0.1",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "http://147.135.212.148/ints/agent/SMSCDRStats",
            "Cookie": f"PHPSESSID={self.session_cookie}",
            "Connection": "keep-alive",
            "DNT": "1",
        }
        current_date = time.strftime("%Y-%m-%d")
        params = {
            "fdate1": f"{current_date} 00:00:00",
            "fdate2": f"{current_date} 23:59:59",
            "frange": "",
            "fclient": "",
            "fnum": "",
            "fcli": "",
            "fgdate": "",
            "fgmonth": "",
            "fgrange": "",
            "fgclient": "",
            "fgnumber": "",
            "fgcli": "",
            "fg": "0",
            "sesskey": "Q05RR0FRUURCUA==",
            "sEcho": "1",
            "iColumns": "9",
            "sColumns": ",,,,,,,,",
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
            "mDataProp_7": "7",
            "sSearch_7": "",
            "bRegex_7": "false",
            "bSearchable_7": "true",
            "bSortable_7": "true",
            "mDataProp_8": "8",
            "sSearch_8": "",
            "bRegex_8": "false",
            "bSearchable_8": "true",
            "bSortable_8": "false",
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
        """aiohttp দিয়ে অ্যাসিঙ্ক ফেচ (দ্রুত)"""
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
                    else:
                        logger.warning(f"HTTP {response.status}: {response.reason}")
                    return None
        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as e:
            logger.warning(f"⚠️ aiohttp ফেচ এরর: {e}")
            return None

    async def _fetch_requests(self, headers, params):
        """requests দিয়ে সিঙ্ক ফেচ (থ্রেড পুলে চলে, ব্লক করে না)"""
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
                else:
                    logger.warning(f"HTTP {response.status_code}: {response.reason}")
            except (requests.RequestException, json.JSONDecodeError) as e:
                logger.warning(f"⚠️ requests ফেচ এরর: {e}")
            return None

        return await asyncio.to_thread(_sync_fetch)

    # ---------- মূল মনিটর লুপ ----------
    async def monitor_loop(self):
        """প্রধান লুপ – প্রতি ০.৫ সেকেন্ডে API চেক করে, প্রথম নতুন OTP পাঠায়"""
        logger.info("🚀 OTP মনিটরিং শুরু – শুধু প্রথম OTP (ইউনিক আইডি অনুযায়ী)")
        await self.send_startup_message()

        consecutive_failures = 0
        retry_delay = 0.5

        while self.is_monitoring:
            try:
                data = await self.fetch_sms_data()

                if data and "aaData" in data:
                    consecutive_failures = 0
                    retry_delay = 0.5

                    sms_list = data["aaData"]
                    valid_sms = [
                        sms for sms in sms_list
                        if len(sms) >= 8 and isinstance(sms[0], str) and ":" in sms[0]
                    ]

                    if valid_sms:
                        # API নতুন আগে দেয়, আমরা উল্টে দিচ্ছি যাতে পুরনো আগে পাই
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

                                # প্রথম নতুন OTP পাঠানোর পর লুপ থেকে বের হয়ে পরবর্তী চক্রের অপেক্ষা
                                break
                        else:
                            logger.debug("ℹ️ এই ব্যাচে কোনো নতুন OTP নেই")
                    else:
                        logger.debug("ℹ️ কোনো বৈধ SMS পাওয়া যায়নি")
                else:
                    consecutive_failures += 1
                    retry_delay = min(retry_delay * 1.5, 5.0)
                    logger.warning(
                        f"⚠️ API এরর বা খালি রেসপন্স। "
                        f"{retry_delay:.1f} সেকেন্ড পর আবার চেষ্টা (ফেইল: {consecutive_failures})"
                    )

                # পরবর্তী চেকের জন্য অপেক্ষা
                await asyncio.sleep(retry_delay if consecutive_failures > 0 else 0.5)

            except asyncio.CancelledError:
                logger.info("🛑 মনিটর লুপ বন্ধ করা হয়েছে")
                break
            except Exception as e:
                logger.exception(f"❌ অপ্রত্যাশিত এরর: {e}")
                await asyncio.sleep(1)


async def main():
    """প্রোগ্রাম এন্ট্রি পয়েন্ট"""
    print("=" * 50)
    print("🤖 OTP মনিটর বট – শুধু প্রথম OTP")
    print("=" * 50)
    print(f"⚡ মোড: প্রথম OTP (ক্রোনোলজিক্যাল)")
    print(f"⏰ চেক ইন্টারভাল: ডায়নামিক (বেস ০.৫ সেকেন্ড)")
    print(f"📱 গ্রুপ আইডি: {GROUP_CHAT_ID}")
    print(f"🌐 টার্গেট URL: {TARGET_URL}")
    print(f"🤖 Number Bot URL: {NUMBER_BOT_URL}")
    print(f"🍪 সেশন কুকি: {SESSION_COOKIE[:10]}...")
    if not HAS_AIOHTTP:
        print("⚠️  aiohttp ইনস্টল নেই – requests ব্যবহার হবে (ধীর). 'pip install aiohttp' দিন দ্রুত অপারেশনের জন্য")
    print("🚀 বট চালু হচ্ছে...")
    print("=" * 50)

    bot = OTPMonitorBot(
        telegram_token=TELEGRAM_BOT_TOKEN,
        group_chat_id=GROUP_CHAT_ID,
        session_cookie=SESSION_COOKIE,
        target_url=TARGET_URL,
    )

    try:
        await bot.monitor_loop()
    except KeyboardInterrupt:
        print("\n🛑 ব্যবহারকারী বট বন্ধ করেছেন!")
        bot.is_monitoring = False
        print(f"📊 সর্বমোট OTP পাঠানো: {bot.total_otps_sent}")
        print("👋 আল্লাহ হাফেজ!")


if __name__ == "__main__":
    asyncio.run(main())