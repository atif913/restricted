# auth.py

import os
import json
from datetime import datetime, timedelta
import asyncio
from config import ADMIN_ID, SESSIONS_DIR, SUB_CLEANUP_INTERVAL

# Path to the JSON file where we store premium grants
AUTH_FILE     = os.path.join(SESSIONS_DIR, "authorized.json")

# In‑memory maps
authorized    = {}      # user_id → { 'expiry': datetime, 'batch_limit': int }
user_tokens   = {}      # user_id → token balance
_credited     = set()   # to avoid double‑crediting referrals

# referral bonus
REFERRAL_BONUS = 3

def _load_authorized():
    try:
        with open(AUTH_FILE, 'r') as f:
            data = json.load(f)
        for uid_str, info in data.items():
            uid = int(uid_str)
            expiry = datetime.fromisoformat(info['expiry'])
            batch_limit = int(info.get('batch_limit', 10))
            authorized[uid] = {'expiry': expiry, 'batch_limit': batch_limit}
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"⚠️ Could not load {AUTH_FILE}: {e}")

    # ensure ADMIN stays authorized
    authorized.setdefault(
        ADMIN_ID,
        {'expiry': datetime.max, 'batch_limit': 10}
    )

def _save_authorized():
    try:
        tmp = {
            str(uid): {
                'expiry': info['expiry'].isoformat(),
                'batch_limit': info['batch_limit']
            }
            for uid, info in authorized.items()
            if info['expiry'] > datetime.utcnow()
        }
        os.makedirs(os.path.dirname(AUTH_FILE), exist_ok=True)
        with open(AUTH_FILE, 'w') as f:
            json.dump(tmp, f, indent=2)
    except Exception as e:
        print(f"⚠️ Could not save {AUTH_FILE}: {e}")

# Load on import
_load_authorized()

async def cleanup_authorized():
    """Purge expired premium grants on schedule."""
    while True:
        now = datetime.utcnow()
        removed = False
        for uid, info in list(authorized.items()):
            if info['expiry'] < now and uid != ADMIN_ID:
                del authorized[uid]
                removed = True
        if removed:
            _save_authorized()
        await asyncio.sleep(SUB_CLEANUP_INTERVAL)

def is_authorized(uid: int) -> bool:
    info = authorized.get(uid)
    return bool(info and info['expiry'] > datetime.utcnow())

def grant_access(uid: int, days: int, batch_limit: int = 10):
    authorized[uid] = {
        'expiry': datetime.utcnow() + timedelta(days=days),
        'batch_limit': batch_limit
    }
    _save_authorized()

def get_batch_limit(uid: int) -> int:
    return authorized.get(uid, {}).get('batch_limit', 10)

# ─── TOKEN & REFERRAL LOGIC ──────────────────────────────────────────

def get_tokens(uid: int) -> int:
    """Return how many free tokens a user has."""
    return user_tokens.get(uid, 0)

def use_token(uid: int) -> bool:
    """Consume one token if available, return True; else False."""
    if user_tokens.get(uid, 0) > 0:
        user_tokens[uid] -= 1
        return True
    return False

def handle_referral(new_uid: int, inviter_uid_s: str) -> bool:
    """
    If this is a valid first‑time referral, credit both
    new_uid and inviter_uid with REFERRAL_BONUS tokens.
    """
    try:
        inviter_uid = int(inviter_uid_s)
    except ValueError:
        return False

    # ignore self‑referral or repeats
    if new_uid == inviter_uid or new_uid in _credited:
        return False

    _credited.add(new_uid)
    user_tokens[inviter_uid] = user_tokens.get(inviter_uid, 0) + REFERRAL_BONUS
    user_tokens[new_uid]      = user_tokens.get(new_uid,      0) + REFERRAL_BONUS
    return True
