"""Store subscription notifications, translated into purchase evidence.

The prototype receives no store notifications. This module is the bounded,
documented map a real integration would apply after verifying a notification
and re-reading the store's current state. It verifies no signature, calls no
store API and is fed only by synthetic scenario controls.

Every row comes from official documentation, checked 2026-10-11 and cited in
docs/PROVIDER_LIFECYCLE_FACTS.md:

- Apple App Store Server Notifications V2: notificationType and subtype.
- Google Play Real-time developer notifications: SubscriptionNotification
  notificationType names. Google says a notification only signals a change;
  subscriptionsv2.get is the source of truth.

The rules that matter most:

- Billing retry outside a grace period grants no access (Apple: an empty
  subtype on DID_FAIL_TO_RENEW means "you can stop providing the subscription
  service"; Google: block access during account hold).
- A grace period grants access until the provider's grace end.
- Pause grants no access; revoke and refund end access at once.
- Some events change nothing about access (Google CANCELED keeps access until
  expiry). They are refused with that reason rather than recorded as a state.

Evidence statuses are provider-neutral. Cross-store merging (a website order
and an App Store purchase on one camera) is this prototype's own policy; no
store documents it.
"""
from typing import Optional

from .engine import DomainError

# What each evidence status means for access. The status a parent sees is
# decided in app.access; this is only whether the store still grants it.
ACCESS = {
    "verified": True,
    "grace": True,          # until the provider's grace end
    "pending": False,
    "unverified": False,
    "billing_retry": False,
    "paused": False,
    "expired": False,
    "refunded": False,
}
# Statuses that only a renewing subscription can have.
SUBSCRIPTION_ONLY = ("grace", "billing_retry", "paused", "expired")

# event -> evidence status; None: no change to record (the reason is in NO_CHANGE).
EVENTS: dict[str, dict[str, Optional[str]]] = {
    "apple": {
        "SUBSCRIBED/INITIAL_BUY": "verified",
        "SUBSCRIBED/RESUBSCRIBE": "verified",
        "DID_RENEW": "verified",
        "DID_RENEW/BILLING_RECOVERY": "verified",
        "DID_FAIL_TO_RENEW/GRACE_PERIOD": "grace",
        "DID_FAIL_TO_RENEW": "billing_retry",
        "GRACE_PERIOD_EXPIRED": "billing_retry",
        "EXPIRED/VOLUNTARY": "expired",
        "EXPIRED/BILLING_RETRY": "expired",
        "EXPIRED/PRICE_INCREASE": "expired",
        "EXPIRED/PRODUCT_NOT_FOR_SALE": "expired",
        "EXPIRED": "expired",
        "REFUND": "refunded",
        "REVOKE": "refunded",
        "DID_CHANGE_RENEWAL_STATUS/AUTO_RENEW_DISABLED": None,
        "DID_CHANGE_RENEWAL_STATUS/AUTO_RENEW_ENABLED": None,
    },
    "google": {
        "SUBSCRIPTION_PURCHASED": "verified",
        "SUBSCRIPTION_RENEWED": "verified",
        "SUBSCRIPTION_RECOVERED": "verified",      # from account hold, or resumed from pause
        "SUBSCRIPTION_RESTARTED": "verified",
        "SUBSCRIPTION_DEFERRED": "verified",       # access continues; the renewal date moves
        "SUBSCRIPTION_IN_GRACE_PERIOD": "grace",
        "SUBSCRIPTION_ON_HOLD": "billing_retry",
        "SUBSCRIPTION_PAUSED": "paused",
        "SUBSCRIPTION_EXPIRED": "expired",
        "SUBSCRIPTION_REVOKED": "refunded",
        "SUBSCRIPTION_CANCELED": None,
        "SUBSCRIPTION_PAUSE_SCHEDULE_CHANGED": None,
    },
}
NO_CHANGE = {
    "DID_CHANGE_RENEWAL_STATUS/AUTO_RENEW_DISABLED": "auto-renew was turned off; access lasts until the period ends",
    "DID_CHANGE_RENEWAL_STATUS/AUTO_RENEW_ENABLED": "auto-renew was turned back on; nothing changes until renewal",
    "SUBSCRIPTION_CANCELED": "the subscription was canceled; access lasts until the period ends",
    "SUBSCRIPTION_PAUSE_SCHEDULE_CHANGED": "a pause was scheduled; access lasts until the next renewal date",
}


def translate(channel: str, event: str) -> str:
    """The evidence status for one store event. Raises for an event this map
    doesn't know and for an event that changes nothing about access."""
    table = EVENTS.get(channel)
    if table is None:
        raise DomainError(422, "NOT_A_STORE_CHANNEL", "only App Store and Google Play events are translated",
                          allowed=sorted(EVENTS))
    if event not in table:
        raise DomainError(422, "UNKNOWN_PROVIDER_EVENT", "this store event isn't in the documented map",
                          allowed=sorted(table))
    status = table[event]
    if status is None:
        raise DomainError(422, "NOT_A_STATE_CHANGE", NO_CHANGE[event])
    return status
