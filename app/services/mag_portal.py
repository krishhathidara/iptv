"""Small, stateless Stalker-style session helper for the local MAG prototype."""

import hashlib
import hmac
import re
import secrets
import time

from app.models import Subscriber

MAC_PATTERN = re.compile(r"[0-9A-F]{2}(?::[0-9A-F]{2}){5}\Z")
SESSION_LIFETIME = 3600


def normalized_mac(value: str | None) -> str | None:
    if not value:
        return None
    mac = value.strip().upper().replace("-", ":")
    return mac if MAC_PATTERN.fullmatch(mac) else None


def matching_mac(subscriber: Subscriber, value: str | None) -> str | None:
    """MACs are self-reported, not cryptographic device identity."""
    mac = normalized_mac(value)
    return mac if mac and subscriber.mac_address and hmac.compare_digest(mac, subscriber.mac_address) else None


def _signature(subscriber: Subscriber, issued: str, mac: str) -> str:
    return hmac.new(subscriber.token_hash.encode("ascii"), f"{issued}.{mac}".encode("ascii"), hashlib.sha256).hexdigest()


def issue_session(subscriber: Subscriber, mac: str) -> str:
    issued = str(int(time.time()))
    return f"{issued}.{_signature(subscriber, issued, mac)}"


def valid_session(subscriber: Subscriber, mac: str, bearer: str | None) -> bool:
    if not bearer or not re.fullmatch(r"[0-9]{10,12}\.[0-9a-f]{64}", bearer):
        return False
    issued, signature = bearer.split(".", 1)
    age = time.time() - int(issued)
    return 0 <= age < SESSION_LIFETIME and secrets.compare_digest(signature, _signature(subscriber, issued, mac))