"""Address blocks, trusted devices and security events; see docs/plans/33-sign-in-attack-protection.md."""
import logging
import secrets
from datetime import timedelta
from ipaddress import IPv6Address, ip_address, ip_network

from django.conf import settings
from django.core import signing
from django.db import connection, transaction
from django.db.models import Q
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from .accounts import clear_rate_limits, count_request, normalize_email
from .models import AddressBlock, Employee, SecurityEvent

logger = logging.getLogger("training.sign_in_protection")

DEVICE_COOKIE = "training_device"
_DEVICE_SALT = "training.trusted_device"
# Counters keyed on the network alone, which lifting a block clears.
NETWORK_SCOPES = ("address_failures", "sign_in_client", "register", "passkey_options")


def client_address(request):
    """The client address. Behind a trusted proxy, the last X-Forwarded-For entry is the client."""
    remote = request.META.get("REMOTE_ADDR", "")
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    try:
        raw, peer = ip_address(remote), _address(remote)
    except ValueError:
        return remote
    # Match trust against both forms, so a network written either way still covers a mapped peer.
    trusted = any(raw in network or peer in network for network in settings.TRUSTED_PROXY_NETWORKS)
    if not forwarded or not trusted:
        return str(peer)
    try:
        # The proxy appends the address it saw, so earlier entries may be forged by the client.
        return str(_address(forwarded.split(",")[-1].strip()))
    except ValueError:
        return str(peer)


def _address(value):
    """Parse an address, reading an IPv4-mapped IPv6 address as IPv4 so one client has one key."""
    address = ip_address(value)
    return getattr(address, "ipv4_mapped", None) or address


def client_network(request):
    """The key for address limits and blocks: the IPv4 address, or the IPv6 /64 a client controls."""
    return network_of(client_address(request))


def network_of(value):
    try:
        address = _address(value)
    except ValueError:
        return value
    if isinstance(address, IPv6Address):
        return str(ip_network(f"{address}/64", strict=False))
    return f"{address}/32"


def normalize_network(value):
    """Read an administrator's or command's network the way blocks store it."""
    value = value.strip()
    if "/" not in value:
        return network_of(value)
    return str(ip_network(value, strict=False))


# Trusted devices

def _fingerprint(employee):
    # Changes when the password changes or the employee is deactivated, ending every trusted device.
    return salted_hmac(_DEVICE_SALT, f"{employee.pk}{employee.password}{employee.session_version}",
                       algorithm="sha256").hexdigest()


def _read_device(request):
    value = request.COOKIES.get(DEVICE_COOKIE)
    if not value:
        return None
    try:
        return signing.loads(value, salt=_DEVICE_SALT, max_age=timedelta(days=settings.TRUSTED_DEVICE_DAYS))
    except signing.BadSignature:
        return None


def trusted_device(request, email):
    """The device ID when this browser has a valid trusted-device cookie for the email, otherwise None."""
    data = _read_device(request)
    if not isinstance(data, dict) or not email:
        return None
    employee = Employee.objects.filter(pk=data.get("e"), is_demo=False, is_active=True).first()
    if employee is None or normalize_email(employee.email) != email:
        return None
    if not constant_time_compare(str(data.get("f", "")), _fingerprint(employee)):
        return None
    return data.get("d")


def set_trusted_device(request, response, employee):
    """Mark this browser as trusted for the employee, keeping its device ID when it already has one."""
    data = _read_device(request)
    device = data.get("d") if isinstance(data, dict) and data.get("e") == str(employee.pk) else None
    value = signing.dumps({"e": str(employee.pk), "d": device or secrets.token_urlsafe(16),
                           "f": _fingerprint(employee)}, salt=_DEVICE_SALT)
    response.set_cookie(DEVICE_COOKIE, value, max_age=settings.TRUSTED_DEVICE_DAYS * 24 * 60 * 60, httponly=True,
                        secure=settings.SESSION_COOKIE_SECURE, samesite="Lax")
    return response


# Address blocks

def _lock(network):
    # Serializes counting, creating and lifting for one network, so they can't interleave.
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [f"address-block:{network}"])


def _open(now):
    return Q(ended_at__isnull=True) & (Q(expires_at__isnull=True) | Q(expires_at__gt=now))


def active_block(network, *, now=None):
    return AddressBlock.objects.filter(_open(now or timezone.now()), network=network).first()


def record_failure(network, *, now=None):
    """Count a failed sign-in for the network and block it once the day's failures pass the limit."""
    now = now or timezone.now()
    with transaction.atomic():
        _lock(network)
        count = count_request("address_failures", network, now=now)
        if count <= settings.ACCOUNT_RATE_LIMITS["address_failures"][0] or active_block(network, now=now):
            return None
        _end_expired(network, now)
        block = AddressBlock.objects.create(network=network, failure_count=count, created_at=now,
                                            expires_at=now + timedelta(hours=settings.ADDRESS_BLOCK_HOURS))
        record_event(SecurityEvent.Kind.BLOCK_CREATED, network=network, detail=f"{count} failed sign-ins",
                     now=now)
    logger.warning("Blocked sign-in from %s after %s failed attempts", network, count)
    return block


def _end_expired(network, now):
    for block in AddressBlock.objects.filter(network=network, ended_at__isnull=True, expires_at__lte=now):
        block.ended_at, block.end_reason = block.expires_at, AddressBlock.EndReason.EXPIRED
        block.save(update_fields=["ended_at", "end_reason"])
        record_event(SecurityEvent.Kind.BLOCK_EXPIRED, network=network, now=now)


def lift_block(network, *, actor=None, now=None):
    """End the network's open block and clear its counters. Returns the lifted block, or None."""
    now = now or timezone.now()
    with transaction.atomic():
        _lock(network)
        _end_expired(network, now)
        block = AddressBlock.objects.filter(network=network, ended_at__isnull=True).first()
        clear_rate_limits(NETWORK_SCOPES, network)
        if block is None:
            return None
        block.ended_at, block.ended_by = now, actor
        block.end_reason = AddressBlock.EndReason.LIFTED if actor else AddressBlock.EndReason.COMMAND
        block.save(update_fields=["ended_at", "ended_by", "end_reason"])
        record_event(SecurityEvent.Kind.BLOCK_LIFTED if actor else SecurityEvent.Kind.BLOCK_COMMAND,
                     actor=actor, network=network, now=now)
    return block


def make_permanent(block_id, *, actor, now=None):
    """Stop an open block from expiring. Returns the block, or None when it has already ended."""
    now = now or timezone.now()
    with transaction.atomic():
        block = AddressBlock.objects.select_for_update().filter(_open(now), pk=block_id).first()
        if block is None:
            return None
        block.expires_at, block.made_permanent_at, block.made_permanent_by = None, now, actor
        block.save(update_fields=["expires_at", "made_permanent_at", "made_permanent_by"])
        record_event(SecurityEvent.Kind.BLOCK_PERMANENT, actor=actor, network=block.network, now=now)
    return block


def blocks_overview(*, now=None):
    now = now or timezone.now()
    return {
        "active_blocks": AddressBlock.objects.filter(_open(now)).order_by("-created_at"),
        "ended_blocks": AddressBlock.objects.filter(~_open(now)).select_related("ended_by")
                        .order_by(Coalesce("ended_at", "expires_at").desc())[:20],
        "events": SecurityEvent.objects.select_related("actor", "employee").order_by("-occurred_at")[:50],
    }


def record_event(kind, *, employee=None, actor=None, network="", detail="", now=None):
    return SecurityEvent.objects.create(kind=kind, employee=employee, actor=actor, network=network, detail=detail,
                                        occurred_at=now or timezone.now())
