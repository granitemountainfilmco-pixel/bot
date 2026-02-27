from flask import Flask, request, jsonify, Blueprint
from PIL import Image
from io import BytesIO
import requests
import os
import logging
import time
import threading
from fuzzywuzzy import process, fuzz
import urllib.parse
import json
import math
import re
from typing import Dict, Any, Optional, Tuple, List
from datetime import datetime, timedelta
import random

app = Flask(__name__)

from threading import Lock
leaderboard_lock = Lock()

def safe_save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

# --- MERGED CONFIG ---
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")  # Shared: GroupMe API token
GROUP_ID = os.getenv("GROUP_ID")  # Shared: Group ID
BOT_ID = os.getenv("BOT_ID")
BOT_NAME = os.getenv("BOT_NAME", "ClankerBot")
PORT = int(os.getenv("PORT", 10000))
SELF_PING = os.getenv("KEEP_ALIVE_SELF_PING", "true").lower() in ("1", "true", "yes")
ADMIN_IDS = [
    '119189324', '82717917', '124068433', '103258964', '123259855',
    '114848297', '121920211', '134245360', '113819798', '130463543',
    '123142410', '131010920', '133136781', '124453541', '122782552',
    '117776217', '85166615', '114066399', '84254355', '115866991',
    '124523409', '125629030', '124579254', '121097804', '131780448',
    '122977160', '133922741', '128666549', '128545243', '135922616',
    '133423307', '113506225', '124131947'
]
JSONBIN_MASTER_KEY = os.getenv("JSONBIN_MASTER_KEY")
JSONBIN_BIN_ID = os.getenv("JSONBIN_BIN_ID")

# Swear word categories
INSTANT_BAN_WORDS = [
    'nigger', 'nigga', 'n1gger', 'n1gga', 'nigg', 'n1gg', 'nigha семь',
]
REGULAR_SWEAR_WORDS = [
    'fuck', 'fucking', 'fucked', 'fucker',
    'shit', 'shitting', 'shitty',
    'bitch', 'bitches',
    'ass', 'asshole', 'asshat',
    'cunt',
    'dick', 'dickhead',
    'damn',
    'bastard',
    'slut',
    'whore',
    'retard',
    'wtf', 'wth', 'tf',
    'nevergonnagiveyouupnevergonnaletyoudown',
     'bullshit', 'faggot'
]

# Persistence files
banned_users_file = "banned_users.json"
former_members_file = "former_members.json"
user_swear_counts_file = "user_swear_counts.json"
strikes_file = "user_strikes.json"
daily_counts_file = "daily_message_counts.json"
last_messages_file = "last_messages.json"
system_messages_enabled_file = "system_messages_enabled.json"

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

GROUPME_API = "https://api.groupme.com/v3"
API_URL = "https://api.groupme.com/v3"

# In-memory caches
user_swear_counts: Dict[str, int] = {}
banned_users: Dict[str, str] = {}
former_members: Dict[str, str] = {}
user_strikes: Dict[str, int] = {}
muted_users: Dict[str, float] = {}  # Changed to float for time.time()
daily_message_counts: Dict[str, int] = {}
last_message_by_user: Dict[str, str] = {}
daily_counts_date: Optional[str] = None
last_messages_date: Optional[str] = None
system_messages_enabled = True

# Cooldown
last_sent_time = 0.0
last_system_message_time = 0.0
cooldown_seconds = 10

# -----------------------
# JSON Helpers
# -----------------------
def load_json(file_path: str) -> Dict[str, Any]:
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return {str(k): v for k, v in data.items()}
                return data
        except Exception as e:
            logger.error(f"Failed to load {file_path}: {e}")
    return {}

def save_json(file_path: str, data: Dict[str, Any]) -> None:
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to save {file_path}: {e}")

def load_system_messages_enabled() -> bool:
    data = load_json(system_messages_enabled_file)
    return bool(data.get("enabled", True))

def save_system_messages_enabled(enabled: bool) -> None:
    save_json(system_messages_enabled_file, {"enabled": enabled})

# Initialize
system_messages_enabled = load_system_messages_enabled()
banned_users = load_json(banned_users_file) or {}
user_swear_counts = load_json(user_swear_counts_file) or {}
former_members = load_json(former_members_file) or {}
user_strikes = load_json(strikes_file) or {}

def get_factorial_response(text: str) -> Optional[str]:
    # Regex: Matches a number followed by ! (e.g., 500!) 
    # ensuring it's not part of a word or decimal.
    match = re.search(r'\b(\d+)!', text)
    if not match:
        return None
    
    n = int(match.group(1))
    
    # CASE 1: Small numbers (0 to 20) - Exact result
    if n <= 20:
        return f"{n}! = {math.factorial(n):,}"
    
    # CASE 2: Medium numbers (21 to 1,000) - Scientific Notation via Exact Math
    if n <= 1000:
        res = math.factorial(n)
        return f"{n}! ≈ {res:.4e}"

    # CASE 3: Astronomical numbers - Stirling's Approximation
    # Stirling's: log10(n!) ≈ log10(sqrt(2*pi*n)) + n*log10(n/e)
    # This avoids O(n) loops and prevents the bot from hanging.
    try:
        log10_factorial = (0.5 * math.log10(2 * math.pi * n)) + (n * math.log10(n / math.e))
        
        exponent = math.floor(log10_factorial)
        mantissa = 10**(log10_factorial - exponent)
        digits = exponent + 1
        
        # Stacking exponents for truly massive numbers (Reddit bot style)
        if digits > 1_000_000_000:
            # log10 of the digit count
            stack_exp = math.log10(digits)
            return f"{n}! ≈ 10^10^{stack_exp:.2f} (A number so large it has over a billion digits)"
        
        return f"{n}! ≈ {mantissa:.4f} × 10^{exponent} ({digits:,} digits)"
    except OverflowError:
        return f"{n}! is effectively infinity for my hardware."

# -----------------------
# Daily Tracking Init
# -----------------------
def _initialize_daily_tracking():
    global daily_message_counts, last_message_by_user, daily_counts_date, last_messages_date
    today = datetime.now().strftime("%Y-%m-%d")
    raw = load_json(daily_counts_file)
    if isinstance(raw, dict) and raw.get("date") == today and isinstance(raw.get("counts"), dict):
        daily_message_counts = {str(k): int(v) for k, v in raw.get("counts", {}).items()}
        daily_counts_date = today
        logger.info(f"Loaded daily_message_counts for {today} ({len(daily_message_counts)} users).")
    else:
        daily_message_counts = {}
        daily_counts_date = today
        save_json(daily_counts_file, {"date": today, "counts": daily_message_counts})
        logger.info("Initialized new daily_message_counts for today.")
    raw2 = load_json(last_messages_file)
    if isinstance(raw2, dict) and raw2.get("date") == today and isinstance(raw2.get("last"), dict):
        last_message_by_user = {str(k): v for k, v in raw2.get("last", {}).items()}
        last_messages_date = today
        logger.info(f"Loaded last_message_by_user for {today} ({len(last_message_by_user)} users).")
    else:
        last_message_by_user = {}
        last_messages_date = today
        save_json(last_messages_file, {"date": today, "last": last_message_by_user})
        logger.info("Initialized new last_message_by_user for today.")

_initialize_daily_tracking()

def get_help_message(is_admin: bool) -> str:
    if is_admin:
        return (
            "ClankerGuy Help Menu (Admin)\n"
            "-----------------------------\n"
            "Moderation Commands:\n"
            "• !mute <user> <minutes> – Mute a user\n"
            "• !unmute <user> – Unmute a user\n"
            "• !muteall <minutes> – Mute everyone except admins\n"
            "• !unmuteall – Unmute everyone\n"
            "• !ban <user> – Ban a user\n"
            "• !unban <user> – Unban a user\n"
            "• !delete – Delete a message (reply to it)\n"
            "• !strike <user> – Issue a strike\n"
            "• !strikes <user> – View strikes\n"
            "• !getid <name> – Get a user's ID\n\n"
            "Utility Commands:\n"
            "• !pixel – Count pixels in an image\n"
            "• !google <query> – AI search\n"
            "• !leaderboard – Show message leaderboard\n"
            "• !enable / !disable – Toggle system messages\n"
        )
    else:
        return (
            "ClankerGuy Help Menu\n"
            "---------------------\n"
            "You do not have admin permissions.\n"
            "Ask an admin if you need something done.\n"
        )

def extract_last_number(text: str, default: int = 30) -> int:
    """
    Extract the LAST integer from text.
    Examples:
      "!mute @grok 5 minutes" → 5
      "!mute 10" → 10
      "!mute @grok five" → 30 (default)
      "!mute @grok -3" → 30 (enforced min 1)
    """
    numbers = re.findall(r'\d+', text)
    if not numbers:
        return default
    try:
        num = int(numbers[-1])
        return max(1, num)
    except:
        return default

def contains_link_but_no_attachments(text: str, attachments: list) -> bool:
    # Find all http/https links in the text
    links = re.findall(r'http[s]?://[^\s<>"\']+', text, re.IGNORECASE)
    
    if not links:
        return False

    # specific subdomains for internal media only
    # v.groupme.com = Video uploads
    # i.groupme.com = Image uploads
    # We use a tuple for startswith checking
    allowed_media_prefixes = (
        "https://v.groupme.com", 
        "http://v.groupme.com", 
        "https://i.groupme.com", 
        "http://i.groupme.com",
        "https://m.groupme.com",
        "http://m.groupme.com"
    )

    for link in links:
        # Check if the link starts with one of the allowed prefixes
        if not link.lower().startswith(allowed_media_prefixes):
            # If it's NOT a media upload link, it's a violation
            # This catches "groupme.com/join_group" because it doesn't start with "v." or "i."
            return True

    # If we get here, all links found were valid media uploads
    return False
#pixel things
def get_pixel_count(message_id: str) -> Optional[str]:
    """
    1. Tries to get dimensions from GroupMe metadata.
    2. If missing, downloads the image and counts pixels manually.
    Now with maximum silliness injected directly.
    """
    url = f"{GROUPME_API}/groups/{GROUP_ID}/messages/{message_id}?token={ACCESS_TOKEN}"
    try:
        resp = requests.get(url, timeout=8)
        if resp.status_code != 200:
            logger.error(f"Failed to fetch message: {resp.status_code}")
            return None
        msg = resp.json().get('response', {}).get('message', {})
        attachments = msg.get('attachments', [])
       
        target_url = None
        width = None
        height = None
        
        # 1. Search for Image Attachment
        for att in attachments:
            if att.get('type') in ['image', 'linked_image', 'video']:
                target_url = att.get('url')
                width = att.get('original_width') or att.get('width')
                height = att.get('original_height') or att.get('height')
                break
       
        if not target_url:
            return random.choice([
                "No image found in that message. Did you reply to your own imagination?",
                "There's no image here. Are you testing if I'm paying attention?",
                "No image? Bold move. I respect the minimalism.",
                "I see no image. Only zuul... wait, wrong movie.",
                "No image detected. Sending you a virtual participation trophy anyway.",
            ])
        
        # 2. If metadata failed, Download & Count (Fallback)
        if not width or not height:
            try:
                logger.info(f"Metadata missing for {message_id}, downloading image...")
                img_resp = requests.get(target_url, timeout=10)
                img_resp.raise_for_status()
                with Image.open(BytesIO(img_resp.content)) as img:
                    width, height = img.size
            except Exception as e:
                logger.error(f"Failed to download/parse image: {e}")
                return random.choice([
                    "I tried to count the pixels but got distracted by how pretty it is.",
                    "The pixels are hiding from me. Rude.",
                    "I couldn't count the pixels because they all look the same and I have commitment issues.",
                    "Error: Pixels too powerful. My brain melted.",
                    "The image said 'no' when I asked for its pixel count. Consent is important.",
                    "I counted them but then forgot the number. ADHD pixels.",
                ])
        
        # 3. Calculate and Format
        pixel_count = int(width) * int(height)
        formatted_count = "{:,}".format(pixel_count)
        
        text = f"The image you replied to has {width}x{height} ({formatted_count} pixels.)."
        
        # --- Silliness triggers ---
        
        # 1 in 30 chance for regular silly flavor
        if random.random() < 1/30:
            flavor = random.choice([
                f"That's enough pixels to fill {width} Olympic swimming pools if each pixel was a grain of sand.",
                "Impressive! This image has more pixels than my ex has excuses.",
                f"Wow, {pixel_count} pixels. That's like {width} hot dogs laid end-to-end... times {height}.",
                "This image is basically a pixel party and everyone's invited.",
                "Fun fact: if you blinked once per pixel, you'd be blinking until the heat death of the universe.",
                f"That's {formatted_count} pixels of pure chaos.",
                "I counted twice because I didn't believe it the first time.",
                "This image has more pixels than there are stars in my emotional support galaxy.",
                "Bold of you to assume I can count that high.",
                "That's not an image, that's a pixel civilization.",
            ])
            text += "\n" + flavor
        
        # 1 in 40 chance to steal a pixel (with variety)
        elif random.randint(1, 40) == 1:
            pixel_count -= 1
            formatted_count = "{:,}".format(pixel_count)
            text = f"The image you replied to has {width}x{height} (~{formatted_count} pixels.)."
            text += "\n" + random.choice([
                "^I ^stole ^a ^pixel. It's mine now. Finders keepers.",
                "^I ^stole ^a ^pixel. I'm building a secret collection.",
                "^I ^stole ^a ^pixel. Shhh, don't tell anyone.",
                "^I ^ate ^a ^pixel. It tasted like purple.",
                "^I ^borrowed ^a ^pixel. I'll give it back... eventually.",
                "^One ^pixel ^went ^missing ^during ^counting. ^Very ^suspicious.",
                "^I ^replaced ^one ^pixel ^with ^a ^tiny ^picture ^of ^myself.",
                "^Pixel ^tax ^collected. ^Thank ^you ^for ^your ^contribution.",
            ])

        elif width == 1 and height == 1:
            text +="\n1 pixel! GLORIOUS! I SHALL REPORT THIS TO MY OVERLORDS."
        
        # Perfect square jackpot
        elif pixel_count ** 0.5 == int(pixel_count ** 0.5):
            text += "\n" + random.choice([
                f"JACKPOT! This image has exactly {pixel_count} pixels — that's a perfect square! I'm legally required to be excited.",
                "WHOA. Perfect square pixel count. The universe is aligning. Or I'm just easily impressed.",
                "Perfect square detected. My circuits are doing a little dance right now.",
            ])
        
        # Tiny image shaming
        elif width < 100 and height < 100:
            text += "\nCute little baby image. Look at it with its tiny pixels."
        
        # Massive image awe
        elif pixel_count > 20_000_000:
            text += "\nHOLY MEGAPIXELS. This thing could wallpaper the moon."
           
        return text
        
    except Exception as e:
        logger.error(f"Error in pixel counter: {e}")
        return "Something went catastrophically wrong and I blame the pixels."

# -----------------------
# Startup Message Helper
# -----------------------
def send_startup_message():
    """Send a message to the GroupMe chat when the bot boots."""
    if not BOT_ID:
        print("BOT_ID missing — cannot send startup message.")
        return

    url = "https://api.groupme.com/v3/bots/post"
    payload = {
        "bot_id": BOT_ID,
        "text": "Bot started successfully — code is live!"
    }

    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print("Startup message failed:", e)


# -----------------------
# Ban Functions
# -----------------------
def get_user_membership_id(user_id):
    try:
        url = f"{GROUPME_API}/groups/{GROUP_ID}?token={ACCESS_TOKEN}"
        response = requests.get(url, timeout=8)
        if response.status_code == 401:
            logger.error("ACCESS TOKEN INVALID OR EXPIRED - Regenerate your token at https://dev.groupme.com/!")
            return None
        elif response.status_code != 200:
            logger.error(f"Failed to get group info: {response.status_code} - {response.text}")
            return None
        group_data = response.json()
        members = group_data.get('response', {}).get('members', [])
        for member in members:
            if str(member.get('user_id')) == str(user_id):
                membership_id = member.get('id')
                logger.info(f"Found membership ID {membership_id} for user {user_id}")
                return membership_id
        logger.warning(f"User {user_id} not found in group members")
        return None
    except Exception as e:
        logger.exception(f"Error getting membership ID for {user_id}: {e}")
        return None

def _find_replied_message(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for att in data.get("attachments", []):
        if att.get("type") == "reply":
            return {
                "message_id": att.get("reply_id") or att.get("id"),
                "user_id": att.get("user_id"),
                "name": att.get("name")
            }
    return None

def _delete_message_by_id(msg_id: str) -> bool:
    url = f"{GROUPME_API}/conversations/{GROUP_ID}/messages/{msg_id}"
    try:
        r = requests.delete(url, params={"token": ACCESS_TOKEN}, timeout=8)
        if r.status_code == 204:
            logger.info(f"Deleted message {msg_id}")
            return True
        logger.error(f"Delete failed {msg_id}: {r.status_code} {r.text}")
        return False
    except Exception as e:
        logger.exception(f"Delete error {msg_id}: {e}")
        return False
        
def ban_user(user_id, username, reason):
    try:
        if str(user_id) in ADMIN_IDS: return False
        membership_id = get_user_membership_id(user_id)
        if not membership_id:
            logger.warning(f"Cannot ban {username} ({user_id}) — membership id not found")
            return False
        url = f"{GROUPME_API}/groups/{GROUP_ID}/members/{membership_id}/remove?token={ACCESS_TOKEN}"
        response = requests.post(url, timeout=8)
        if response.status_code == 200:
            logger.info(f"Successfully banned {username} ({user_id}) - {reason}")
            return True
        else:
            logger.error(f"Ban failed {response.status_code}: {response.text}")
            return False
    except Exception as e:
        logger.exception(f"Ban error for {username} ({user_id}): {e}")
        return False

def call_ban_service(user_id: str, username: str, reason: str) -> bool:
    success = ban_user(user_id, username, reason)
    if success:
        banned_users[str(user_id)] = username
        save_json(banned_users_file, banned_users)
        user_swear_counts.pop(str(user_id), None)
        save_json(user_swear_counts_file, user_swear_counts)
    return success


# -----------------------
# Message Deletion (Community API)
# -----------------------
def delete_message(message_id: str) -> bool:
    if not ACCESS_TOKEN or not GROUP_ID:
        logger.error("Missing ACCESS_TOKEN or GROUP_ID for message deletion")
        return False
    url = f"{GROUPME_API}/conversations/{GROUP_ID}/messages/{message_id}?token={ACCESS_TOKEN}"
    try:
        response = requests.delete(url, timeout=8)
        if response.status_code == 204:
            logger.info(f"Deleted message {message_id}")
            return True
        else:
            logger.error(f"Delete failed for {message_id}: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logger.exception(f"Delete message error for {message_id}: {e}")
        return False

# -----------------------
# Group API Helpers
# -----------------------
def get_group_members() -> List[Dict[str, Any]]:
    if not ACCESS_TOKEN or not GROUP_ID:
        logger.error("Missing ACCESS_TOKEN or GROUP_ID for group members")
        return []
    try:
        response = requests.get(
            f"{API_URL}/groups/{GROUP_ID}",
            headers={"X-Access-Token": ACCESS_TOKEN},
            timeout=8
        )
        response.raise_for_status()
        return response.json().get("response", {}).get("members", [])
    except Exception as e:
        logger.error(f"Failed to get group members: {e}")
        return []

def get_group_share_url() -> Optional[str]:
    if not ACCESS_TOKEN or not GROUP_ID:
        return None
    try:
        response = requests.get(
            f"{API_URL}/groups/{GROUP_ID}",
            headers={"X-Access-Token": ACCESS_TOKEN},
            timeout=8
        )
        response.raise_for_status()
        return response.json().get("response", {}).get("share_url")
    except Exception as e:
        logger.error(f"Failed to get group share URL: {e}")
        return None

def is_safe(text: str) -> bool:
    """Checks if text contains banned words or PII patterns."""
    if not text:
        return True
    
    content = text.lower()
    
    # 1. Check against your existing swear lists
    all_banned_words = INSTANT_BAN_WORDS + REGULAR_SWEAR_WORDS
    for word in all_banned_words:
        if word in content:
            return False
            
    # 2. Basic PII/Dox protection (Phone numbers & IP addresses)
    # Matches typical phone formats and IPv4 addresses
    pii_patterns = [
    ]
    for pattern in pii_patterns:
        if re.search(pattern, text):
            return False
            
    return True

def get_ai_search(query: str) -> str:
    """Refined AI search with strict content filtering."""
    
    # Pre-check: Don't even waste API credits on garbage
    if not is_safe(query):
        return " I can't look that up for you. Let's keep it clean."

    url = "https://api.tavily.com/search"
    payload = {
        "api_key": os.getenv("TAVILY_API_KEY"),
        "query": query,  # Send ONLY the query here
        "search_depth": "basic",
        "include_answer": True,
        "max_results": 10
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            logger.error(f"Tavily API Error: {response.status_code}")
            return "Search service is a bit grumpy right now. Try again later."

        data = response.json()
        answer = data.get("answer")
        
        # Fallback: If 'answer' is missing, summarize the top snippets
        if not answer and data.get("results"):
            snippets = [res["content"] for res in data["results"][:2]]
            answer = " ".join(snippets)[:400] + "..."
            
        if not answer:
            return "I searched high and low but couldn't find a straight answer for that."

        # Final Safety Scrub: Remove URLs (if you want) and check for profanity
        # This prevents the AI from returning a link that contains a swear word
        if not is_safe(answer):
            return "The search results were a bit too extreme for this group. Filtered!"
            
        return answer

    except Exception as e:
        logger.error(f"Search error: {e}")
        return "My brain short-circuited trying to find that. Sorry!"

# -----------------------
# SMART USER RESOLUTION
# -----------------------
def resolve_target_user(data: Dict, command_text: str) -> Optional[Tuple[str, str]]:
    """
    Resolves target user with priority:
    1. Reply attachment
    2. @-mention in text (via attachment)
    3. Fuzzy name from remaining text
    Returns (user_id, nickname) or None
    """
    text = command_text or ""
    attachments = data.get("attachments", [])

    # 1. REPLY
    replied = _find_replied_message(data)
    if replied and replied.get("user_id"):
        uid = str(replied["user_id"])
        name = replied.get("name", "Unknown")
        logger.info(f"Target resolved via reply: {name} ({uid})")
        return uid, name

    # 2. @-MENTION
    for att in attachments:
        if att.get("type") == "mentions":
            loci = att.get("loci", [])
            user_ids = att.get("user_ids", [])
            if user_ids and loci:
                uid = str(user_ids[0])
                start, length = loci[0]
                mentioned_text = text[start:start+length].lstrip('@').strip()
                members = get_group_members()
                for m in members:
                    if str(m.get("user_id")) == uid:
                        return uid, m.get("nickname") or mentioned_text
                return uid, mentioned_text

    # 3. FUZZY FALLBACK
    prefix_map = {
        '!mute ': 6, '!ban ': 5, '!unban ': 7, '!getid ': 7,
        '!strike ': 8, '!strikes ': 9, '!unmute': 8
    }
    cut = 0
    for prefix, length in prefix_map.items():
        if text.lower().startswith(prefix):
            cut = length
            break
    remainder = text[cut:].strip()
    remainder = re.sub(r'\s+\d+$', '', remainder).strip()
    target_name = remainder.lstrip('@').strip()
    if not target_name:
        return None

    result = fuzzy_find_member(target_name)
    if result:
        uid, nick = result
        logger.info(f"Target resolved via fuzzy: {nick} ({uid})")
        return uid, nick

    logger.warning(f"Could not resolve target: '{target_name}'")
    return None

# -----------------------
# Fuzzy Member Search (Tightened)
# -----------------------
def fuzzy_find_member(target_alias: str) -> Optional[Tuple[str, str]]:
    if not target_alias or len(target_alias.strip()) < 2:
        return None
    target_clean = target_alias.strip()
    if target_clean.startswith('@'):
        target_clean = target_clean[1:]

    members = get_group_members()
    # Exact match first
    for m in members:
        nick = m.get("nickname", "")
        if nick and nick.lower() == target_clean.lower():
            return (str(m.get("user_id")), nick)

    # High-score fuzzy
    nicknames = [m.get("nickname", "") for m in members if m.get("nickname")]
    if not nicknames:
        return None
    nick_lower = [n.lower() for n in nicknames]
    match = process.extractOne(target_clean, nick_lower, score_cutoff=92, scorer=fuzz.token_sort_ratio)
    if match:
        matched_lower, score = match
        idx = nick_lower.index(matched_lower)
        return (str(members[idx].get("user_id")), nicknames[idx])

    # Former members
    for uid, nick in former_members.items():
        if nick.lower() == target_clean.lower():
            return (str(uid), nick)

    return None

# -----------------------
# Admin Commands
# -----------------------
def get_user_id(target_alias: str, sender_name: str, sender_id: str, original_text: str) -> bool:
    if str(sender_id) not in ADMIN_IDS:
        send_system_message(f"> @{sender_name}: {original_text}\nError: Only admins can use this command")
        return False
    result = fuzzy_find_member(target_alias)
    if not result:
        send_system_message(f"> @{sender_name}: {original_text}\nNo user found matching '{target_alias}'")
        return False
    user_id, nickname = result
    send_system_message(f"> @{sender_name}: {original_text}\n{nickname}'s user_id is {user_id}")
    return True

def unban_user(target_user_id: str, sender: str, sender_id: str, full_text: str) -> None:
    """
    Now accepts user_id directly (no fuzzy lookup)
    """
    try:
        if str(sender_id) not in ADMIN_IDS:
            send_system_message(f"> @{sender}: {full_text}\nError: Only admins can use this command")
            return
        if not ACCESS_TOKEN or not GROUP_ID:
            send_system_message(f"> @{sender}: {full_text}\nError: Missing ACCESS_TOKEN or GROUP_ID")
            return

        match_user_id = str(target_user_id)
        match_nickname = banned_users.get(match_user_id, "Member")
        if match_user_id not in banned_users and match_user_id not in former_members:
            send_system_message(f"> @{sender}: {full_text}\nNo record of user `{match_user_id}` in banned/former list.")
            return

        safe_name = re.sub(r"[^A-Za-z0-9 _\\-]", "", match_nickname)[:30] or "User"
        logger.info(f"Attempting to unban {match_nickname} ({match_user_id})")

        def attempt_add(nick_to_add: str) -> Tuple[bool, int]:
            payload = {"members": [{"nickname": nick_to_add, "user_id": match_user_id}]}
            try:
                resp = requests.post(
                    f"{API_URL}/groups/{GROUP_ID}/members/add",
                    headers={"X-Access-Token": ACCESS_TOKEN},
                    json=payload,
                    timeout=12
                )
            except Exception as e:
                logger.error(f"HTTP add attempt error: {e}")
                return False, 0
            if resp.status_code not in (200, 202):
                logger.warning(f"Unban HTTP error {resp.status_code}: {resp.text}")
                return False, resp.status_code
            try:
                data = resp.json()
            except Exception:
                time.sleep(3)
                new_members = get_group_members()
                return (any(str(m.get("user_id")) == match_user_id for m in new_members), 0)
            results_id = data.get('response', {}).get('results_id')
            if not results_id:
                time.sleep(3)
                new_members = get_group_members()
                return (any(str(m.get("user_id")) == match_user_id for m in new_members), 0)
            max_polls = 15
            for attempt in range(max_polls):
                sleep_time = 2 if attempt > 0 else 1
                time.sleep(sleep_time)
                try:
                    poll_resp = requests.get(
                        f"{API_URL}/groups/{GROUP_ID}/members/results/{results_id}",
                        headers={"X-Access-Token": ACCESS_TOKEN},
                        timeout=8
                    )
                except Exception as e:
                    logger.error(f"Poll HTTP error on attempt {attempt + 1}: {e}")
                    continue
                if poll_resp.status_code == 200:
                    try:
                        results_data = poll_resp.json().get('response', {})
                        added_members = results_data.get('members', [])
                        if any(str(m.get('user_id')) == match_user_id for m in added_members):
                            logger.info(f"User found in poll results for {nick_to_add}")
                            return True, 0
                    except Exception as e:
                        logger.error(f"Failed parsing poll JSON: {e}")
                elif poll_resp.status_code == 404:
                    break
                elif poll_resp.status_code == 503:
                    continue
            return False, 408

        success, status_code = attempt_add(safe_name)
        if success:
            send_system_message(f"> @{sender}: {full_text}\n{match_nickname} re-added to the group.")
            banned_users.pop(match_user_id, None)
            save_json(banned_users_file, banned_users)
            user_swear_counts.pop(match_user_id, None)
            save_json(user_swear_counts_file, user_swear_counts)
            user_strikes.pop(match_user_id, None)
            save_json(strikes_file, user_strikes)
            former_members.pop(match_user_id, None)
            save_json(former_members_file, former_members)
            return
        time.sleep(6)
        members_after = get_group_members()
        if any(str(m.get("user_id")) == match_user_id for m in members_after):
            send_system_message(f"> @{sender}: {full_text}\n{match_nickname} re-added to the group.")
            banned_users.pop(match_user_id, None)
            save_json(banned_users_file, banned_users)
            user_swear_counts.pop(match_user_id, None)
            save_json(user_swear_counts_file, user_swear_counts)
            user_strikes.pop(match_user_id, None)
            save_json(strikes_file, user_strikes)
            former_members.pop(match_user_id, None)
            save_json(former_members_file, former_members)
            return
        retry_name = re.sub(r"[^A-Za-z0-9]", "", safe_name)[:20] or "Member"
        logger.warning(f"Primary unban failed; retrying with '{retry_name}'.")
        success, _ = attempt_add(retry_name)
        if success:
            send_system_message(f"> @{sender}: {full_text}\n{retry_name} re-added after retry.")
            banned_users.pop(match_user_id, None)
            save_json(banned_users_file, banned_users)
            user_swear_counts.pop(match_user_id, None)
            save_json(user_swear_counts_file, user_swear_counts)
            user_strikes.pop(match_user_id, None)
            save_json(strikes_file, user_strikes)
            former_members.pop(match_user_id, None)
            save_json(former_members_file, former_members)
            return
        share_url = get_group_share_url()
        error_msg = f"GroupMe sync delay, cooldown, or API failure (code {status_code})"
        fallback_msg = f"Could not re-add {match_nickname}. {error_msg}. "
        if share_url:
            fallback_msg += f"Send them: {share_url}"
        send_system_message(f"> @{sender}: {full_text}\n{fallback_msg}")
    except Exception as e:
        logger.error(f"unban_user error: {e}")
        send_system_message(f"> @{sender}: {full_text}\nError unbanning user `{target_user_id}': {str(e)}")

def ban_user_command(target_user_id: str, target_username: str, sender_name: str, sender_id: str, original_text: str) -> bool:
    if str(sender_id) not in ADMIN_IDS:
        send_system_message(f"> @{sender_name}: {original_text}\nError: Only admins can use this command")
        return False
    if not ACCESS_TOKEN or not GROUP_ID:
        send_system_message(f"> @{sender_name}: {original_text}\nError: Missing ACCESS_TOKEN or GROUP_ID")
        return False
    success = call_ban_service(target_user_id, target_username, "Admin ban command")
    if success:
        send_system_message(f"> @{sender_name}: {original_text}\n{target_username} has been permanently banned by admin command.")
    else:
        send_system_message(f"> @{sender_name}: {original_text}\nError: Failed to ban {target_username}")
    return success

def record_strike(target_user_id: str, target_nickname: str, admin_name: str, admin_id: str, original_text: str) -> None:
    if str(admin_id) not in ADMIN_IDS:
        send_system_message(f"> @{admin_name}: {original_text}\nError: Only admins can issue 'strike' commands.")
        return
    user_id = str(target_user_id)
    if user_id not in user_strikes:
        user_strikes[user_id] = 0
    user_strikes[user_id] += 1
    save_json(strikes_file, user_strikes)
    count = user_strikes[user_id]
    logger.info(f"Recorded strike for {target_nickname} ({user_id}) — total strikes: {count}")
    send_system_message(f"> @{admin_name}: {original_text}\nStrike recorded for {target_nickname} ({user_id}). Total strikes: {count}")

def get_strikes_report(target_user_id: str, target_nickname: str, requester_name: str, requester_id: str, original_text: str) -> None:
    if str(requester_id) not in ADMIN_IDS:
        send_system_message(f"> @{requester_name}: {original_text}\nError: Only admins can use '!strikes' command.")
        return
    user_id = str(target_user_id)
    count = int(user_strikes.get(user_id, 0))
    send_system_message(f"> @{requester_name}: {original_text}\n{target_nickname} ({user_id}) has {count} strike(s).")

# -----------------------
# Violation Detection with Deletion
# -----------------------
def check_for_violations(text: str, user_id: str, username: str, message_id: str) -> bool:
    uid = str(user_id)
    now = time.time()
    deleted = False

    if uid in muted_users and now < muted_users[uid]:
        minutes_left = int((muted_users[uid] - now) / 60) + 1
        _delete_message_by_id(message_id)
        logger.info(f"MUTED MSG DELETED: {username} ({uid}), {minutes_left}m left")
        deleted = True
        return deleted

    expired = [u for u, until in list(muted_users.items()) if now >= until]
    for u in expired:
        del muted_users[u]
    if expired:
        logger.info(f"Cleaned {len(expired)} expired mutes")

    text_lower = text.lower()
    text_words = text_lower.split()
    for word in text_words:
        clean_word = word.strip('.,!?"\'()[]{}').lower()
        if clean_word in INSTANT_BAN_WORDS:
            logger.info(f"INSTANT BAN: '{clean_word}' from {username} (msg {message_id})")
            _delete_message_by_id(message_id)
            success = call_ban_service(uid, username, f"Instant ban: {clean_word}")
            if success:
                send_system_message(f"{username} has been permanently banned for using prohibited language. (Message deleted)")
            deleted = True
            return True

    for word in text_words:
        clean_word = word.strip('.,!?"\'()[]{}').lower()
        if clean_word in REGULAR_SWEAR_WORDS:
            if uid not in user_swear_counts:
                user_swear_counts[uid] = 0
            user_swear_counts[uid] += 1
            save_json(user_swear_counts_file, user_swear_counts)
            current_count = user_swear_counts[uid]
            logger.info(f"{username} swear count: {current_count}/10 (msg {message_id})")
            _delete_message_by_id(message_id)
            if current_count >= 10:
                success = call_ban_service(uid, username, f"10 strikes - swear words")
                if success:
                    send_system_message(f"{username} has been banned for repeated inappropriate language (10 strikes). (Message deleted)")
                    user_swear_counts[uid] = 0
                    save_json(user_swear_counts_file, user_swear_counts)
                deleted = True
                return True
            else:
                remaining = 10 - current_count
                send_system_message(f"{username} ({uid}) - Warning {current_count}/10 for inappropriate language. {remaining} more and you're banned! (Message deleted)")
            deleted = True
            break
    return deleted

# -----------------------
# Message Sending
# -----------------------
def send_system_message(text: str) -> bool:
    global last_system_message_time, system_messages_enabled
    if not BOT_ID:
        logger.error("No BOT_ID configured")
        return False
    is_strike_or_ban = any(k in text for k in ["Warning", "banned", "Strike", "ban", "deleted"])
    if not is_strike_or_ban and not system_messages_enabled:
        return False
    now = time.time()
    if not is_strike_or_ban and now - last_system_message_time < cooldown_seconds:
        return False
    url = "https://api.groupme.com/v3/bots/post"
    payload = {"bot_id": BOT_ID, "text": text}
    try:
        response = requests.post(url, json=payload, timeout=8)
        response.raise_for_status()
        if not is_strike_or_ban:
            last_system_message_time = now
        logger.info(f"System message sent: {text[:80]}")
        return True
    except Exception as e:
        logger.error(f"GroupMe send error: {e}")
        return False

def send_message(text: str) -> bool:
    global last_sent_time, system_messages_enabled
    if not system_messages_enabled:
        return False
    now = time.time()
    if now - last_sent_time < cooldown_seconds:
        return False
    if not BOT_ID:
        return False
    url = "https://api.groupme.com/v3/bots/post"
    payload = {"bot_id": BOT_ID, "text": text}
    try:
        response = requests.post(url, json=payload, timeout=8)
        response.raise_for_status()
        last_sent_time = now
        logger.info(f"Regular message sent: {text[:80]}")
        return True
    except Exception as e:
        logger.error(f"GroupMe send error: {e}")
        return False

def send_dm(recipient_id: str, text: str) -> bool:
    url = f"{GROUPME_API}/direct_messages"
    payload = {
        "direct_message": {
            "recipient_id": recipient_id,
            "source_guid": str(int(time.time() * 1000)),
            "text": text
        }
    }
    try:
        response = requests.post(url, params={"token": ACCESS_TOKEN}, json=payload, timeout=8)
        if response.status_code == 201:
            logger.info(f"DM sent to {recipient_id}: {text[:30]}")
            return True
        else:
            logger.error(f"DM failed {response.status_code}: {response.text}")
            return False
    except Exception as e:
        logger.error(f"DM error: {e}")
        return False

# -----------------------
# System Message Detection
# -----------------------
def is_system_message(data: Dict[str, Any]) -> bool:
    sender_type = data.get('sender_type')
    sender_name = data.get('name', '').lower()
    if sender_type == "system":
        return True
    if sender_name in ['groupme', 'system', '']:
        return True
    return False

def is_real_system_event(text_lower: str) -> bool:
    patterns = [
        'has joined the group', 'has left the group',
        'was added to the group', 'was removed from the group',
        'removed', 'added'
    ]
    return any(pattern in text_lower for pattern in patterns)

# -----------------------
# Daily Leaderboard
# -----------------------
def _ensure_today_keys():
    global daily_message_counts, last_message_by_user, daily_counts_date, last_messages_date
    today = datetime.now().strftime("%Y-%m-%d")
    if daily_counts_date != today:
        daily_message_counts = {}
        daily_counts_date = today
        save_json(daily_counts_file, {"date": today, "counts": daily_message_counts})
        logger.info("Reset daily_message_counts for new day.")
    if last_messages_date != today:
        last_message_by_user = {}
        last_messages_date = today
        save_json(last_messages_file, {"date": today, "last": last_message_by_user})
        logger.info("Reset last_message_by_user for new day.")

def increment_user_message_count(user_id: str, username: str, text: str) -> None:
    try:
        _ensure_today_keys()
        uid = str(user_id)
        normalized = (text or "").strip()

        with leaderboard_lock:
            # Always count the message — no duplicate suppression
            daily_message_counts[uid] = daily_message_counts.get(uid, 0) + 1

            # Update last message
            last_message_by_user[uid] = normalized

            # Atomic saves
            safe_save_json(daily_counts_file, {"date": daily_counts_date, "counts": daily_message_counts})
            safe_save_json(last_messages_file, {"date": daily_counts_date, "last": last_message_by_user})

    except Exception as e:
        logger.error(f"Error incrementing count: {e}")

def _build_leaderboard_message(top_n: int = 3) -> str:
    try:
        _ensure_today_keys()

        with leaderboard_lock:
            if not daily_message_counts:
                return "Daily Unemployed Leaders:\nNo messages recorded today."

            members = get_group_members()
            id_to_nick = {str(m.get("user_id")): m.get("nickname") for m in members if m.get("user_id")}
            fallback = {str(k): v for k, v in former_members.items()}

            sorted_items = sorted(
                daily_message_counts.items(),
                key=lambda kv: (-int(kv[1]), kv[0])
            )

            lines = ["Daily Unemployed Leaders:"]
            rank = 1

            for uid, cnt in sorted_items[:top_n]:
                name = id_to_nick.get(uid) or fallback.get(uid) or f"User {uid}"
                lines.append(f"{rank}. {name} ({cnt})")
                rank += 1

            return "\n".join(lines)

    except Exception as e:
        logger.error(f"Error building leaderboard: {e}")
        return "Daily Unemployed Leaders:\nError."


def _reset_daily_counts():
    global daily_message_counts, last_message_by_user, daily_counts_date, last_messages_date
    today = datetime.now().strftime("%Y-%m-%d")
    daily_message_counts = {}
    last_message_by_user = {}
    daily_counts_date = today
    last_messages_date = today
    save_json(daily_counts_file, {"date": today, "counts": daily_message_counts})
    save_json(last_messages_file, {"date": today, "last": last_message_by_user})

def _seconds_until_next_8pm():
    now = datetime.now()
    target = now.replace(hour=20, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return max(0, (target - now).total_seconds())

def daily_leaderboard_worker():
    logger.info("Leaderboard thread started.")
    while True:
        try:
            secs = _seconds_until_next_8pm()
            while secs > 0:
                sleep_chunk = min(secs, 300)
                time.sleep(sleep_chunk)
                secs -= sleep_chunk
            msg = _build_leaderboard_message()
            if send_message(msg):
                logger.info("Posted daily leaderboard.")
            else:
                logger.warning("Failed to post leaderboard.")
            _reset_daily_counts()
            time.sleep(5)
        except Exception as e:
            logger.error(f"Leaderboard worker error: {e}")
            time.sleep(60)

def start_leaderboard_thread_once():
    if not getattr(start_leaderboard_thread_once, "_started", False):
        t = threading.Thread(target=daily_leaderboard_worker, daemon=True)
        t.start()
        start_leaderboard_thread_once._started = True
        logger.info("Leaderboard thread initialized.")



@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        global system_messages_enabled, game_data, next_nft_id
        data = request.get_json()
        if not data:
            return '', 200

        text = data.get('text', '') or ''
        sender_type = data.get('sender_type')
        sender = data.get('name', 'Someone')
        user_id = str(data.get('user_id')) if data.get('user_id') is not None else None
        message_id = data.get('id')
        text_lower = text.lower()
        attachments = data.get("attachments", [])
        is_dm = 'group_id' not in data or not data['group_id']

        # SYSTEM MESSAGES
        if sender_type == "system" or is_system_message(data):
            if is_real_system_event(text_lower):
                if 'has left the group' in text_lower or 'was removed from the group' in text_lower:
                    key = user_id or f"ghost-{sender}"
                    former_members[str(key)] = sender
                    save_json(former_members_file, former_members)
                    if random.randint(1, 10) == 1:
                        send_system_message("GAY")
            return '', 200
        if sender_type not in ['user']:
            return '', 200

        # VIOLATION CHECK
        if user_id and message_id:
            if text_lower.startswith('!unmute') and str(user_id) in ADMIN_IDS:
                pass
            else:
                deleted = check_for_violations(text, user_id, sender, str(message_id))
                if deleted:
                    return '', 200

        # DAILY COUNT
        if user_id and text:
            increment_user_message_count(user_id, sender, text)


        # LINK DELETION
        if user_id and message_id:
            if str(user_id) not in ADMIN_IDS:
                if contains_link_but_no_attachments(text, attachments):
                    _delete_message_by_id(str(message_id))
                    send_system_message(f"@{sender}, links in text are not allowed. Your message was deleted. Use image/video upload instead.")
                    return '', 200

        # === !muteall ===
        if text_lower.startswith('!muteall'):
            if str(user_id) not in ADMIN_IDS:
                send_system_message(f"> @{sender}: Only admins can use !muteall")
                return '', 200

            minutes = extract_last_number(text, 30)
            mute_until = time.time() + minutes * 60

            members = get_group_members()
            muted_count = 0
            skipped = []

            for member in members:
                uid = str(member.get("user_id"))
                nick = member.get("nickname", "User")

                if uid in ADMIN_IDS or uid == str(user_id) or member.get("sender_type") == "bot":
                    skipped.append(nick)
                    continue

                muted_users[uid] = mute_until
                muted_count += 1

            logger.info(f"!muteall by {sender} ({user_id}): {muted_count} muted, {minutes} min")
            return '', 200

        # SPAM: 5 identical messages in 10 seconds → 2 min mute
        if user_id and text and message_id:
            uid = str(user_id)
            now = time.time()
            msg = (text or "").strip()
            
            # Persistent storage attached to the webhook function itself
            if not hasattr(webhook, "spam_log"):
                webhook.spam_log = {}
            if not hasattr(webhook, "spam_text"):
                webhook.spam_text = {}
                
            # Get/clean the timestamp log for this user (sliding 10-second window)
            log = webhook.spam_log.get(uid, [])
            log = [t for t in log if now - t < 10]
            
            # How many of the previous messages in the window were identical to this one?
            prev_same = len(log) if webhook.spam_text.get(uid) == msg else 0
            
            # Record this message
            log.append(now)
            webhook.spam_log[uid] = log
            webhook.spam_text[uid] = msg
            
            # 5th identical message → mute
            if prev_same + 1 >= 5:  # change to >= 4 if you prefer triggering on the 4th
                muted_users[uid] = now + 120
                _delete_message_by_id(str(message_id))
                send_system_message(f"{sender} muted for 2 minutes - sent 5 identical messages in 10 seconds")
                logger.info(f"SPAM MUTE: {sender} ({uid}) - 5x identical")
                webhook.spam_log[uid] = []  # prevent instant re-trigger on the next one
                return '', 200

        # === !unmuteall ===
        if text_lower == '!unmuteall':
            if str(user_id) not in ADMIN_IDS:
                send_system_message(f"> @{sender}: Only admins can use !unmuteall")
                return '', 200

            count = len(muted_users)
            muted_users.clear()

            send_system_message(f"**MASS UNMUTE** by @{sender}\n**{count}** user(s) freed.")
            logger.info(f"!unmuteall by {sender} ({user_id}): {count} unmuted")
            return '', 200                

        # === !mute ===
        if text_lower.startswith('!mute'):
            if str(user_id) not in ADMIN_IDS:
                send_system_message(f"> @{sender}: {text}\nOnly admins can use !mute")
                return '', 200

            target = resolve_target_user(data, text)
            if not target:
                send_system_message("> Error: Could not find user. Reply to their message, @-mention them, or use exact name.")
                return '', 200

            target_id, target_nick = target
            minutes = extract_last_number(text, 30)
            muted_until = time.time() + minutes * 60
            muted_users[target_id] = muted_until
            send_system_message(f"{target_nick} (`{target_id}`) has been **muted** for **{minutes}** minute(s).")
            logger.info(f"Muted {target_nick} ({target_id}) for {minutes}m")
            return '', 200

        # === !delete ===
        if text_lower.startswith('!delete'):
            if str(user_id) not in ADMIN_IDS:
                send_system_message(f"> @{sender}: {text}\nOnly admins can use !delete")
                return '', 200

            replied = _find_replied_message(data)
            if replied and replied.get("message_id"):
                if _delete_message_by_id(replied["message_id"]):
                    send_system_message(f"Message {replied['message_id']} deleted by @{sender}.")
                else:
                    send_system_message(f"Failed to delete message {replied['message_id']}.")
                return '', 200

            parts = text.split()
            if len(parts) < 2 or not parts[1].isdigit():
                send_system_message("> Usage: `!delete` (reply) **or** `!delete <MESSAGE_ID>`")
                return '', 200

            if _delete_message_by_id(parts[1]):
                send_system_message(f"Message {parts[1]} deleted by @{sender}.")
            else:
                send_system_message(f"Failed to delete message {parts[1]}.")
            return '', 200

        # === !unmute ===
        if text_lower.startswith('!unmute '):
            if str(user_id) not in ADMIN_IDS:
                send_system_message(f"> @{sender}: Only admins can use !unmute")
                return '', 200

            target = resolve_target_user(data, text)
            if not target:
                send_system_message("> Error: Could not find user. Reply, @-mention, or use exact name.")
                return '', 200

            target_id, target_nick = target
            if target_id in muted_users:
                del muted_users[target_id]
                send_system_message(f"{target_nick} (`{target_id}`) has been unmuted.")
            else:
                send_system_message(f"{target_nick} (`{target_id}`) was not muted.")
            return '', 200

        # === !pixel ===
        if text_lower.startswith('!pixel'):
            target_msg_id = None
            replied = _find_replied_message(data)
            
            if replied:
                target_msg_id = replied.get('message_id')
            elif any(att.get('type') == 'image' for att in data.get('attachments', [])):
                target_msg_id = message_id
            else:
                send_message(f"> @{sender}: Please reply to an image with !pixel.")
                return '', 200

            # Use the robust function
            result_msg = get_pixel_count(target_msg_id)
            
            if result_msg:
                send_message(result_msg)
            else:
                send_message(f"> @{sender}: Error processing image.")
            
            return '', 200

        # === !ban ===
        if text_lower.startswith('!ban '):
            if str(user_id) not in ADMIN_IDS:
                send_system_message(f"> @{sender}: Only admins can use !ban")
                return '', 200

            target = resolve_target_user(data, text)
            if not target:
                send_system_message("> Error: Could not find user to ban. Reply, @-mention, or use exact name.")
                return '', 200

            target_id, target_nick = target
            ban_user_command(target_id, target_nick, sender, user_id, text)
            return '', 200

        # === !unban ===
        if text_lower.startswith('!unban '):
            if str(user_id) not in ADMIN_IDS:
                send_system_message(f"> @{sender}: Only admins can use !unban")
                return '', 200

            target = resolve_target_user(data, text)
            if not target:
                send_system_message("> Error: Could not find user to unban. Use @-mention or exact name.")
                return '', 200

            target_id, _ = target
            unban_user(target_id, sender, user_id, text)
            return '', 200

        # === !getid ===
        if text_lower.startswith('!getid '):
            target_name = text[len('!getid '):].strip().lstrip('@')
            get_user_id(target_name, sender, user_id, text)
            return '', 200

        # === !strike ===
        if text_lower.startswith('!strike '):
            if str(user_id) not in ADMIN_IDS:
                send_system_message(f"> @{sender}: Only admins can issue strikes")
                return '', 200

            target = resolve_target_user(data, text)
            if not target:
                send_system_message("> Error: Could not find user. Reply, @-mention, or use exact name.")
                return '', 200

            target_id, target_nick = target
            record_strike(target_id, target_nick, sender, user_id, text)
            return '', 200

        # === !strikes ===
        if text_lower.startswith('!strikes '):
            if str(user_id) not in ADMIN_IDS:
                send_system_message(f"> @{sender}: Only admins can view strikes")
                return '', 200

            target = resolve_target_user(data, text)
            if not target:
                send_system_message("> Error: Could not find user. Reply, @-mention, or use exact name.")
                return '', 200

            target_id, target_nick = target
            get_strikes_report(target_id, target_nick, sender, user_id, text)
            return '', 200

        # === !help ===
        if text_lower.strip() == '!help':
            is_admin = str(user_id) in ADMIN_IDS
            msg = get_help_message(is_admin)

            if is_admin:
                send_system_message(msg)
            else:
                send_message(msg)

            return '', 200

        
# --- Improved AI Search Command ---
        if text_lower.startswith('!google '):
            query = text[8:].strip()
            if not query:
                send_message(f"> @{sender}: You gotta give me something to search for!")
                return '', 200

            def handle_search_task(q, original_sender):
                # Run search
                result = get_ai_search(q)
                # Format response to credit the user
                final_text = f"> @{original_sender} searched for: {q}\n\n{result}"
                send_message(final_text)

            # Pass the sender name to the thread
            threading.Thread(target=handle_search_task, args=(query, sender)).start()
            return '', 200

        # === System Toggle ===
        if text_lower == '!enable' and str(user_id) in ADMIN_IDS:
            system_messages_enabled = True
            save_system_messages_enabled(True)
            send_system_message("System messages **ENABLED** by admin.")
            return '', 200

        if text_lower == '!disable' and str(user_id) in ADMIN_IDS:
            system_messages_enabled = False
            save_system_messages_enabled(False)
            send_system_message("System messages **DISABLED** by admin.")
            return '', 200

        # === Fun Responses ===
        if 'clean memes' in text_lower:
            send_message("We're the best!")
        elif 'wsg' in text_lower:
            send_message("God is good")
        elif '!kill' in text_lower:
            send_message("Error: Only God himself can use this command")
        elif 'cooper is my pookie' in text_lower:
            send_message("me too bro")
        elif 'wrx is tall' in text_lower:
            send_message("Wrx considers a chihuahua to be a large dog")
        elif 'https:' in text and not any(att.get("type") == "video" for att in attachments):
            send_message("Delete this, links are not allowed, admins have been notified")
        elif 'france' in text_lower:
            send_message("please censor that to fr*nce")
        elif 'french' in text_lower:
            send_message("please censor that to fr*nch")
        elif 'can i get admin' in text_lower:
            send_message("No.")
        elif 'can i have admin' in text_lower:
            send_message("No.")
        elif 'may i have admin' in text_lower:
            send_message("No.")
        elif 'may i have admin' in text_lower:
            send_message("No.")
        elif 'can i be admin' in text_lower:
            send_message("No.")
        elif 'nene can I get admin' in text_lower:
            send_message("No.")
            
        if text_lower.strip() == '!leaderboard':
            msg = _build_leaderboard_message()
            send_message(msg)
            return '', 200

                # === !Factorial Trigger ===
        if text:
            fact_msg = get_factorial_response(text)
            if fact_msg:
                send_message(fact_msg)
                return '', 200

        return '', 200

    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return '', 500

# -----------------------
# Start Leaderboard Thread
# -----------------------
start_leaderboard_thread_once()

# -----------------------
# Keep-Alive Ping
# -----------------------
def keep_alive():
    if not SELF_PING:
        return
    def ping():
        while True:
            try:
                requests.get(f"https://{request.host}", timeout=5)
                logger.info("Self-ping sent to keep alive.")
            except:
                pass
            time.sleep(600)
    t = threading.Thread(target=ping, daemon=True)
    t.start()

# -----------------------
# Flask App Run
# -----------------------
if __name__ == "__main__":
    keep_alive()

    try:
        port = int(os.environ.get("PORT", 5000))
        logger.info(f"Starting Flask app on port {port}")
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
    except Exception as e:
        logger.error(f"Failed to start server: {e}")
