from flask import Flask, request
import requests
import os
import time
import logging
import threading
from collections import deque
from fuzzywuzzy import process

app = Flask(__name__)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config (same env vars you already use)
BOT_ID = os.getenv("BOT_ID")
BOT_NAME = os.getenv("BOT_NAME", "ClankerBot")
GROUP_ID = os.getenv("GROUP_ID")
BAN_SERVICE_URL = os.getenv("BAN_SERVICE_URL")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")  # GroupMe API token for member list

# Admins
ADMIN_IDS = [
    '119189324', '82717917', '124068433', '103258964', '123259855',
    '114848297', '121920211', '134245360', '113819798', '130463543',
    '123142410', '131010920', '133136781', '124453541', '122782552',
    '117776217', '85166615', '114066399', '84254355', '115866991',
    '125629030'
]

# Swear word categories
INSTANT_BAN_WORDS = [
    'nigger', 'nigga', 'n1gger', 'n1gga', 'nigg', 'n1gg'
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
    'wtf',
    'nevergonnagiveyouupnevergonnaletyoudown',
    '67'
]

# Track user swear counts and banned users (in memory)
user_swear_counts = {}
banned_users = {}     # {user_id: username}
former_members = {}   # {user_id: username} - for anyone removed/left/banned

# Member cache to remember recent user_id <-> nickname pairs
# NOTE: we now also store membership ids (GroupMe member record 'id') which are required for removal.
member_cache = {}            # nickname.lower() -> user_id   (backwards-compatible)
id_to_name = {}              # user_id -> nickname
membership_by_nick = {}      # nickname.lower() -> membership_id
membership_by_userid = {}    # user_id -> membership_id

# Cooldowns
last_sent_time = 0
last_system_message_time = 0
cooldown_seconds = 10

# Ban queue & worker settings (tune these if you keep hitting 429)
BAN_QUEUE_INTERVAL = float(os.getenv("BAN_QUEUE_INTERVAL", 3.0))  # seconds between queued ban attempts
BAN_MAX_RETRIES = int(os.getenv("BAN_MAX_RETRIES", 3))

# Simple queue for bans to prevent bursts that cause 429s
ban_queue = deque()
ban_queue_lock = threading.Lock()
ban_worker_running = False

# -----------------------
# Member cache utilities
# -----------------------

def refresh_member_cache():
    """Fetch current group members and refresh the local cache (including membership IDs)."""
    if not ACCESS_TOKEN or not GROUP_ID:
        return
    try:
        r = requests.get(
            f"https://api.groupme.com/v3/groups/{GROUP_ID}",
            headers={"X-Access-Token": ACCESS_TOKEN},
            timeout=8
        )
        r.raise_for_status()
        members = r.json().get("response", {}).get("members", [])
        # clear previous caches to avoid stale conflicts
        # (we merge so other code keeps working)
        for m in members:
            nick = m.get("nickname")
            uid = m.get("user_id")
            mid = m.get("id")  # <-- membership id (required for remove)
            if nick and uid:
                member_cache[nick.lower()] = uid
                id_to_name[uid] = nick
            if nick and mid:
                membership_by_nick[nick.lower()] = mid
            if uid and mid:
                membership_by_userid[uid] = mid
    except Exception as e:
        logger.debug(f"refresh_member_cache failed: {e}")

def remember_member(nickname, user_id):
    """Remember a member from an incoming message (helps recover IDs on leave)."""
    if not nickname or not user_id:
        return
    try:
        member_cache[nickname.lower()] = user_id
        id_to_name[user_id] = nickname
    except Exception:
        pass

# -----------------------
# Ban queue worker
# -----------------------

def _start_ban_worker_once():
    global ban_worker_running
    if ban_worker_running:
        return
    ban_worker_running = True
    t = threading.Thread(target=_ban_worker, daemon=True)
    t.start()

def _ban_worker():
    """Worker that processes ban requests from queue at controlled intervals."""
    logger.info("Ban worker started")
    while True:
        item = None
        with ban_queue_lock:
            if ban_queue:
                item = ban_queue.popleft()
        if not item:
            time.sleep(0.5)
            continue

        user_id = item.get("user_id")
        username = item.get("username")
        reason = item.get("reason", "Manual ban")
        attempts = item.get("attempts", 0)

        if not user_id:
            logger.error("Ban worker got queue item with no user_id, skipping")
            continue

        # perform actual HTTP call to BAN_SERVICE_URL
        if not BAN_SERVICE_URL:
            logger.error("No BAN_SERVICE_URL configured - can't process queued ban")
            continue

        payload = {
            'user_id': user_id,
            'username': username,
            'membership_id': membership_by_userid.get(user_id) or membership_by_nick.get((username or "").lower()),
            'reason': reason
        }

        try:
            r = requests.post(f"{BAN_SERVICE_URL}/ban", json=payload, timeout=8)
            logger.info(f"Ban worker POST {payload} -> status {r.status_code}")
            if r.status_code == 200:
                banned_users[user_id] = username
                former_members[user_id] = username
                # notify group about the ban (worker does the final notification)
                send_system_message(f"🔨 {username} has been banned. (processed)")
                logger.info(f"Banned {username} ({user_id}) from queue")
            elif r.status_code == 429:
                # respect Retry-After if provided
                retry_after = r.headers.get("Retry-After")
                wait = float(retry_after) if retry_after and retry_after.isdigit() else (2 ** min(attempts, 6))
                logger.warning(f"Ban worker got 429; will re-enqueue after {wait}s (headers: {r.headers})")
                # re-enqueue with incremented attempts
                item['attempts'] = attempts + 1
                # simple backoff: wait then push back
                time.sleep(wait)
                with ban_queue_lock:
                    ban_queue.appendleft(item)
            else:
                logger.error(f"Ban worker non-200: {r.status_code} {r.text} headers={r.headers}")
                send_system_message(f"Error: failed to ban {username} (status {r.status_code})")
        except Exception as e:
            logger.error(f"Ban worker exception: {e}")
            # re-enqueue with backoff a few times, otherwise give up
            item['attempts'] = attempts + 1
            if item['attempts'] <= BAN_MAX_RETRIES:
                backoff = 2 ** item['attempts']
                logger.info(f"Re-enqueueing ban for {username} after exception, backoff {backoff}s")
                time.sleep(backoff)
                with ban_queue_lock:
                    ban_queue.appendleft(item)
            else:
                logger.error(f"Giving up banning {username} after {item['attempts']} attempts")
                send_system_message(f"Error: failed to ban {username} after retries")
        # rate-limit between queue items
        time.sleep(BAN_QUEUE_INTERVAL)

def enqueue_ban(user_id, username, reason="Manual ban"):
    """Put a ban into the queue and start worker if needed."""
    with ban_queue_lock:
        ban_queue.append({'user_id': user_id, 'username': username, 'reason': reason, 'attempts': 0})
    _start_ban_worker_once()
    logger.info(f"Enqueued ban for {username} ({user_id})")
    send_system_message(f"🔨 Ban for {username} queued (processing...)")

# -----------------------
# Ban service + lookups
# -----------------------

def call_ban_service(user_id, username, reason="Manual ban"):
    """
    Try an immediate ban call to BAN_SERVICE_URL (if configured).
    On 429 or repeated transient errors we enqueue the ban for safe processing.
    Returns True if immediate success, False otherwise (may be queued).
    """
    if not BAN_SERVICE_URL:
        logger.warning("No BAN_SERVICE_URL configured - can't ban users")
        return False

    # ensure we have membership mapping where possible
    if user_id not in membership_by_userid:
        # try to refresh the cache once to populate membership ids
        refresh_member_cache()

    payload = {
        'user_id': user_id,
        'username': username,
        'membership_id': membership_by_userid.get(user_id) or membership_by_nick.get((username or "").lower()),
        'reason': reason
    }

    try:
        r = requests.post(f"{BAN_SERVICE_URL}/ban", json=payload, timeout=6)
        if r.status_code == 200:
            logger.info(f"✅ Banned {username} ({user_id}): {reason}")
            banned_users[user_id] = username
            former_members[user_id] = username
            return True
        elif r.status_code == 429:
            # log headers for debugging (Retry-After etc)
            logger.warning(f"❌ Ban failed 429: Too Many Requests; headers={r.headers} body={r.text}")
            # try to honor Retry-After if present then enqueue as fallback
            retry_after = r.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                wait = int(retry_after)
                logger.info(f"Respecting Retry-After: sleeping {wait}s before enqueueing")
                time.sleep(wait)
            # enqueue for worker (safer) and notify
            enqueue_ban(user_id, username, reason)
            return False
        else:
            logger.error(f"❌ Ban failed {r.status_code}: {r.text} headers={r.headers}")
            # if there was a client/server error (5xx), we can enqueue too, or return False
            if 500 <= r.status_code < 600:
                enqueue_ban(user_id, username, reason)
            return False
    except requests.RequestException as e:
        logger.error(f"❌ Ban service error (request exception): {e}")
        # network or transient error -> enqueue and retry via worker
        enqueue_ban(user_id, username, reason)
        return False
    except Exception as e:
        logger.error(f"❌ Ban service unexpected error: {e}")
        return False

# -----------------------
# Existing helpers (unchanged behavior, minor tweaks)
# -----------------------

def get_user_id(target_alias, sender_name, sender_id, original_text):
    """Admin-only helper that publishes the resolved user_id (uses fuzzy match on live group members)."""
    if sender_id not in ADMIN_IDS:
        send_system_message(f"> @{sender_name}: {original_text}\nError: Only admins can use this command")
        return False
    if not ACCESS_TOKEN or not GROUP_ID:
        send_system_message(f"> @{sender_name}: {original_text}\nError: Missing ACCESS_TOKEN or GROUP_ID")
        logger.error("Missing ACCESS_TOKEN or GROUP_ID for user_id query")
        return False

    refresh_member_cache()
    if not member_cache:
        send_system_message(f"> @{sender_name}: {original_text}\nError: No members found")
        return False

    aliases = list(member_cache.keys())  # lowercase nicknames
    best_match = process.extractOne(target_alias.lower(), aliases, score_cutoff=80)
    if not best_match:
        send_system_message(f"> @{sender_name}: {original_text}\nNo user found matching '{target_alias}'")
        return False

    matched_nick = best_match[0]
    user_id = member_cache.get(matched_nick)
    real_name = id_to_name.get(user_id, matched_nick)
    if user_id:
        send_system_message(f"> @{sender_name}: {original_text}\n{real_name}'s user_id is {user_id}")
        return True

    send_system_message(f"> @{sender_name}: {original_text}\nError: Could not retrieve user_id")
    return False

def unban_user(target_alias, sender_name, sender_id, original_text):
    """Unban a user by fuzzy nickname or by user_id (admin-only)."""
    if sender_id not in ADMIN_IDS:
        send_system_message(f"> @{sender_name}: {original_text}\nError: Only admins can use this command")
        return False
    if not ACCESS_TOKEN or not GROUP_ID:
        send_system_message(f"> @{sender_name}: {original_text}\nError: Missing ACCESS_TOKEN or GROUP_ID")
        logger.error("Missing ACCESS_TOKEN or GROUP_ID for unban")
        return False

    # Build candidate map: nickname.lower() -> (uid, uname) and uid string -> (uid, uname)
    candidates = {}
    for uid, uname in banned_users.items():
        if uname:
            candidates[uname.lower()] = (uid, uname)
        candidates[str(uid)] = (uid, uname)
    for uid, uname in former_members.items():
        if uname:
            candidates[uname.lower()] = (uid, uname)
        candidates[str(uid)] = (uid, uname)

    if not candidates:
        send_system_message(f"> @{sender_name}: {original_text}\nError: No known banned or removed users to unban")
        return False

    lookup_key = target_alias.strip()
    key_lower = lookup_key.lower()
    if key_lower in candidates:
        target_user_id, target_username = candidates[key_lower]
    elif lookup_key in candidates:  # check raw (for user_id strings)
        target_user_id, target_username = candidates[lookup_key]
    else:
        # Fuzzy-match only against the nickname keys (avoid matching numeric IDs fuzzily)
        nickname_keys = [k for k in candidates.keys() if not k.isdigit()]
        if not nickname_keys:
            send_system_message(f"> @{sender_name}: {original_text}\nError: No nickname records to match against.")
            return False
        best = process.extractOne(key_lower, nickname_keys)
        if not best or best[1] < 70:
            send_system_message(f"> @{sender_name}: {original_text}\nError: No banned/removed user found matching '{target_alias}'")
            return False
        matched_nick = best[0]
        target_user_id, target_username = candidates[matched_nick]

    # Re-add user via GroupMe API
    url = f"https://api.groupme.com/v3/groups/{GROUP_ID}/members/add"
    payload = {"members": [{"user_id": target_user_id, "nickname": target_username}]}
    headers = {"X-Access-Token": ACCESS_TOKEN}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=5)
        response.raise_for_status()
        res_data = response.json().get("response", {}) if response.text else {}
        results_id = res_data.get("results_id")
        if results_id:
            results_url = f"https://api.groupme.com/v3/groups/{GROUP_ID}/members/results/{results_id}"
            for _ in range(6):
                r = requests.get(results_url, headers=headers, timeout=5)
                r.raise_for_status()
                res = r.json().get("response", {})
                if res.get("status") in ["success", "complete"]:
                    logger.info(f"✅ Unbanned {target_username} ({target_user_id})")
                    send_system_message(f"> @{sender_name}: {original_text}\n✅ {target_username} has been unbanned and re-added to the group")
                    banned_users.pop(target_user_id, None)
                    former_members.pop(target_user_id, None)
                    return True
                members_status = res.get("members", [])
                if any(m.get("status") in ["added", "success"] for m in members_status):
                    logger.info(f"✅ Unbanned (member-status) {target_username} ({target_user_id})")
                    send_system_message(f"> @{sender_name}: {original_text}\n✅ {target_username} has been unbanned and re-added to the group")
                    banned_users.pop(target_user_id, None)
                    former_members.pop(target_user_id, None)
                    return True
                time.sleep(1)
            send_system_message(f"> @{sender_name}: {original_text}\nError: Timed out confirming unban for {target_username}")
            return False
        else:
            logger.info(f"✅ Unbanned {target_username} ({target_user_id}) (no results_id)")
            send_system_message(f"> @{sender_name}: {original_text}\n✅ {target_username} has been unbanned and re-added to the group")
            banned_users.pop(target_user_id, None)
            former_members.pop(target_user_id, None)
            return True

    except Exception as e:
        logger.error(f"❌ Unban error: {e}")
        send_system_message(f"> @{sender_name}: {original_text}\nError: Failed to unban '{target_alias}'")
        return False

# -----------------------
# Moderation / messaging
# -----------------------

def check_for_violations(text, user_id, username):
    text_lower = text.lower()
    text_words = text_lower.split()
    
    for word in text_words:
        clean_word = word.strip('.,!?"\'()[]{}').lower()
        if clean_word in INSTANT_BAN_WORDS:
            logger.info(f"🚨 INSTANT BAN: '{clean_word}' from {username}")
            # call_ban_service will attempt immediate ban; if rate-limited it will enqueue and worker will finish it
            call_ban_service(user_id, username, "Instant ban: " + clean_word)
            return True
    
    for word in text_words:
        clean_word = word.strip('.,!?"\'()[]{}').lower()
        if clean_word in REGULAR_SWEAR_WORDS:
            if user_id not in user_swear_counts:
                user_swear_counts[user_id] = 0
            user_swear_counts[user_id] += 1
            current_count = user_swear_counts[user_id]
            logger.info(f"{username} swear count: {current_count}/10")
            
            if current_count >= 10:
                call_ban_service(user_id, username, "10 strikes - swear words")
                user_swear_counts[user_id] = 0
                return True
            else:
                remaining = 10 - current_count
                send_system_message(f"⚠️ {username} ({user_id}) - Warning {current_count}/10 for inappropriate language. {remaining} more and you're banned!")
            break
    return False

def send_system_message(text):
    global last_system_message_time
    if not BOT_ID:
        logger.error("No BOT_ID configured - can't send messages")
        return False
    
    is_strike_message = "Warning" in text or "banned" in text
    now = time.time()
    if not is_strike_message and now - last_system_message_time < cooldown_seconds:
        logger.info("System message cooldown active")
        return False

    url = "https://api.groupme.com/v3/bots/post"
    payload = {"bot_id": BOT_ID, "text": text}
    try:
        response = requests.post(url, json=payload, timeout=5)
        response.raise_for_status()
        if not is_strike_message:
            last_system_message_time = now
        logger.info(f"System message sent: {text[:60]}...")
        return True
    except Exception as e:
        logger.error(f"GroupMe system send error: {e}")
        return False

def send_message(text):
    global last_sent_time
    now = time.time()
    if now - last_sent_time < cooldown_seconds:
        logger.info("Regular cooldown active")
        return False
    if not BOT_ID:
        logger.error("No BOT_ID configured - can't send messages")
        return False
    url = "https://api.groupme.com/v3/bots/post"
    payload = {"bot_id": BOT_ID, "text": text}
    try:
        response = requests.post(url, json=payload, timeout=5)
        response.raise_for_status()
        last_sent_time = now
        logger.info(f"Regular message sent: {text[:60]}...")
        return True
    except Exception as e:
        logger.error(f"GroupMe send error: {e}")
        return False

# -----------------------
# Helpers for system detection
# -----------------------

def is_system_message(data):
    sender_type = data.get('sender_type')
    sender_name = data.get('name', '').lower()
    if sender_type == "system":
        return True
    system_senders = ['groupme', 'system', '']
    if sender_name in system_senders:
        return True
    return False

def is_real_system_event(text_lower):
    system_patterns = [
        'has joined the group',
        'has left the group',
        'was added to the group',
        'was removed from the group',
        'removed',
        'added'
    ]
    return any(pattern in text_lower for pattern in system_patterns)

# -----------------------
# Webhook endpoint
# -----------------------

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data or 'text' not in data:
            return '', 200
        text = data['text']
        sender_type = data.get('sender_type')
        sender = data.get('name', 'Someone')
        user_id = data.get('user_id')
        text_lower = text.lower()
        attachments = data.get("attachments", [])
        
        # If it's a user message, remember them in the cache so we can resolve later if they leave
        if sender_type == 'user' and sender and user_id:
            remember_member(sender, user_id)

        # SYSTEM MESSAGE HANDLING
        if sender_type == "system" or is_system_message(data):
            if is_real_system_event(text_lower):
                if 'has left the group' in text_lower:
                    # Prefer explicit user_id if provided; otherwise attempt to resolve from cache by name
                    if user_id:
                        former_members[user_id] = sender
                    else:
                        # Try fuzzy match against our cache
                        refresh_member_cache()
                        if member_cache:
                            best = process.extractOne(sender.lower(), list(member_cache.keys()))
                            if best and best[1] >= 70:
                                resolved_uid = member_cache.get(best[0])
                                if resolved_uid:
                                    former_members[resolved_uid] = id_to_name.get(resolved_uid, sender)
                                else:
                                    former_members[f"ghost-{sender}"] = sender
                            else:
                                former_members[f"ghost-{sender}"] = sender
                        else:
                            former_members[f"ghost-{sender}"] = sender
                    send_system_message("GAY")
                elif 'has joined the group' in text_lower:
                    send_system_message("Welcome to Clean Memes, check the rules and announcement topics before chatting!")
                elif 'was removed from the group' in text_lower or 'removed' in text_lower:
                    # same resolution attempt for removed
                    if user_id:
                        former_members[user_id] = sender
                    else:
                        refresh_member_cache()
                        if member_cache:
                            best = process.extractOne(sender.lower(), list(member_cache.keys()))
                            if best and best[1] >= 70:
                                resolved_uid = member_cache.get(best[0])
                                if resolved_uid:
                                    former_members[resolved_uid] = id_to_name.get(resolved_uid, sender)
                                else:
                                    former_members[f"ghost-{sender}"] = sender
                            else:
                                former_members[f"ghost-{sender}"] = sender
                        else:
                            former_members[f"ghost-{sender}"] = sender
                    send_system_message("this could be you if you break the rules, watch it. 👀")
            return '', 200
        
        # Skip non-user messages
        if sender_type not in ['user']:
            return '', 200
        
        # Unban command
        if text_lower.startswith('!unban '):
            target_alias = text_lower.replace('!unban ', '').strip()
            if target_alias:
                unban_user(target_alias, sender, user_id, text)
            return '', 200
        
        # User ID query
        if text_lower.startswith('!userid ') or ('what is' in text_lower and 'user id' in text_lower):
            target_alias = text_lower.replace('!userid ', '').strip()
            if 'what is' in text_lower:
                target_alias = text_lower.split('user id')[0].replace('what is', '').strip()
            if target_alias:
                get_user_id(target_alias, sender, user_id, text)
            return '', 200
        
        # Ban checks
        if user_id and text:
            check_for_violations(text, user_id, sender)
        
        # User triggers (keep unchanged)
        if 'clean memes' in text_lower:
            send_message("We're the best!")
        elif 'wsg' in text_lower:
            send_message("God is good")
        elif 'cooper is my pookie' in text_lower:
            send_message("me too bro")
        elif 'https:' in text and not any(att.get("type") == "video" for att in attachments):
            send_message("Delete this, links are not allowed, admins have been notified")
        
        return '', 200
        
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return '', 500

@app.route('/reset-count', methods=['POST'])
def reset_count():
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        if not user_id:
            return {"error": "user_id required"}, 400
        old_count = user_swear_counts.get(user_id, 0)
        user_swear_counts[user_id] = 0
        return {"success": True, "old_count": old_count, "new_count": 0}
    except Exception as e:
        logger.error(f"Reset count error: {e}")
        return {"error": str(e)}, 500

@app.route('/health', methods=['GET'])
def health():
    return {
        "status": "healthy" if BOT_ID else "missing config",
        "bot_id": BOT_ID[:8] + "..." if BOT_ID else "MISSING",
        "bot_name": BOT_NAME,
        "ban_system": {
            "enabled": bool(GROUP_ID and BAN_SERVICE_URL),
            "group_id": GROUP_ID or "MISSING",
            "ban_service_url": BAN_SERVICE_URL or "MISSING",
            "instant_ban_words": len(INSTANT_BAN_WORDS),
            "regular_swear_words": len(REGULAR_SWEAR_WORDS),
            "tracked_users": len(user_swear_counts),
            "banned_users": len(banned_users),
            "former_members": len(former_members)
        },
        "system_triggers": {
            "left": "GAY",
            "joined": "Welcome message",
            "removed": "Rules warning"
        }
    }

# Start bot
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    logger.info("🚀 Starting ClankerBot (no AI)")
    logger.info(f"Bot ID: {'SET' if BOT_ID else 'MISSING'}")
    logger.info(f"Bot Name: {BOT_NAME}")
    logger.info(f"Ban System: {'ENABLED' if GROUP_ID and BAN_SERVICE_URL else 'DISABLED'}")
    # start ban worker thread so queue will process if something enqueues on startup
    _start_ban_worker_once()
    app.run(host="0.0.0.0", port=port, debug=False)
