# Provider lifecycle facts

Verified provider semantics for subscription billing and row locks.
Use this file to model lifecycle states honestly in the prototype.

- Date checked for every row: **2026-10-11**.
- Sources: developer.apple.com, developer.android.com, postgresql.org only.
- No nanit.com host was fetched. RevenueCat was not used.
- Quotes are short excerpts or close paraphrases. Paraphrases are marked "(para.)".
- "Not verified" means no official sentence was found. Do not claim it.
- Apple pages render with JavaScript. Text was read via the same page's
  documentation JSON (`developer.apple.com/tutorials/data/documentation/<path>.json`).
  The human URL is cited below.

Short names for cited URLs:

| Key | URL |
|---|---|
| A-NT | https://developer.apple.com/documentation/appstoreservernotifications/notificationtype |
| A-ST | https://developer.apple.com/documentation/appstoreservernotifications/subtype |
| A-BILL | https://developer.apple.com/documentation/storekit/handling-subscriptions-billing |
| A-GP | https://developer.apple.com/help/app-store-connect/manage-subscriptions/enable-billing-grace-period-for-auto-renewable-subscriptions |
| A-STATUS | https://developer.apple.com/documentation/appstoreserverapi/status |
| A-GASS | https://developer.apple.com/documentation/appstoreserverapi/get-all-subscription-statuses |
| A-GPED | https://developer.apple.com/documentation/appstoreserverapi/graceperiodexpiresdate |
| A-RETRY | https://developer.apple.com/documentation/appstoreserverapi/isinbillingretryperiod |
| A-EXP | https://developer.apple.com/documentation/appstoreserverapi/expiresdate |
| A-RESP | https://developer.apple.com/documentation/appstoreservernotifications/responding-to-app-store-server-notifications |
| A-RECV | https://developer.apple.com/documentation/appstoreservernotifications/receiving-app-store-server-notifications |
| A-SP | https://developer.apple.com/documentation/appstoreservernotifications/signedpayload |
| A-JWSH | https://developer.apple.com/documentation/appstoreserverapi/jwsdecodedheader |
| A-OTID | https://developer.apple.com/documentation/appstoreserverapi/originaltransactionid |
| A-OID | https://developer.apple.com/documentation/storekit/transaction/originalid |
| A-AAT | https://developer.apple.com/documentation/appstoreserverapi/appaccounttoken |
| A-TAAT | https://developer.apple.com/documentation/storekit/transaction/appaccounttoken |
| A-REVD | https://developer.apple.com/documentation/storekit/transaction/revocationdate |
| A-REFN | https://developer.apple.com/documentation/storekit/handling-refund-notifications |
| G-LC | https://developer.android.com/google/play/billing/lifecycle/subscriptions |
| G-RTDN | https://developer.android.com/google/play/billing/rtdn-reference |
| PG-LOCK | https://www.postgresql.org/docs/17/explicit-locking.html |
| PG-93 | https://www.postgresql.org/docs/9.3/release-9-3.html |

---

## 1. Apple App Store

| State | Official name | Access? | What ends it | Provider dates | Source + quote | Checked |
|---|---|---|---|---|---|---|
| Active / renewed | `DID_RENEW` (no subtype); status `1` | Yes | Failed renewal, expiry, refund | `expiresDate` per transaction; new transaction each renewal | A-NT: "Provide the customer with access to the subscription's content or service." A-EXP: "When the auto-renewable subscription renews, the App Store creates a new transaction" | 2026-10-11 |
| Billing grace period | `DID_FAIL_TO_RENEW` + `GRACE_PERIOD`; status `4` | Yes | Recovery, or grace expiry | `gracePeriodExpiresDate` in renewal info | A-ST: "Continue to provide access to the subscription during the grace period." A-GPED: "The time when the Billing Grace Period for subscription renewals expires." | 2026-10-11 |
| Grace length options | App Store Connect setting | n/a | n/a | 3, 16 or 28 days. Weekly plans cap at 6 days | A-GP: "select a period of 3, 16, or 28 days" | 2026-10-11 |
| Grace period expired | `GRACE_PERIOD_EXPIRED` | No (may stop) | Apple keeps retrying billing | Retry continues; see max retry row | A-NT: grace "ended without renewing the subscription, so you can turn off access" | 2026-10-11 |
| Billing retry, no grace | `DID_FAIL_TO_RENEW` (no subtype); status `3` | No (may stop) | Recovery, retry window end, or cancel | `isInBillingRetryPeriod` = true | A-NT: "If the subtype is empty ... you can stop providing the subscription service." | 2026-10-11 |
| Max billing retry duration | Billing retry period | n/a | 60 days, fix, or cancel | 60 days | A-NT: "The App Store continues to retry billing for 60 days" (para.: or until fixed/canceled). A-BILL: "attempts to renew the subscription for up to 60 days." | 2026-10-11 |
| Billing recovery | `DID_RENEW` + `BILLING_RECOVERY` | Yes, restore | Normal lifecycle resumes | New transaction with new `expiresDate` (para.) | A-ST: "the expired subscription that previously failed to renew has successfully renewed." | 2026-10-11 |
| Voluntary expiration | `EXPIRED` + `VOLUNTARY`; status `2` | No | Terminal for this period; resubscribe sends `SUBSCRIBED`/`RESUBSCRIBE` | `expiresDate` of last transaction | A-ST: "expired after the customer turned off subscription auto-renewal." | 2026-10-11 |
| Expired after billing retry | `EXPIRED` + `BILLING_RETRY`; status `2` | No | Terminal | Last `expiresDate` | A-ST: "failed to renew before the billing retry period ended." | 2026-10-11 |
| Other expirations | `EXPIRED` + `PRICE_INCREASE` / `PRODUCT_NOT_FOR_SALE` / none | No | Terminal | Last `expiresDate` | A-NT (para.): no subtype means "expired for some other reason". | 2026-10-11 |
| Auto-renew turned off | `DID_CHANGE_RENEWAL_STATUS` + `AUTO_RENEW_DISABLED` | Yes until `expiresDate` (para.) | Expiry, or re-enable | `expiresDate` | A-NT (para.): customer turned off renewal, or App Store did after a refund request. Access-until-expiry wording: **not verified** on this page. | 2026-10-11 |
| Refund | `REFUND`; status `5` | No (revoked) | Terminal for that transaction | `revocationDate`, `revocationReason` | A-NT (para.): refund of consumable, non-consumable, auto-renewable or non-renewing. A-STATUS: "The App Store refunded the transaction or revoked it from Family Sharing." | 2026-10-11 |
| Family Sharing revoke | `REVOKE`; status `5` | No for the family member | Terminal for sharing | `revocationDate` | A-NT (para.): shared purchase "no longer available through sharing". | 2026-10-11 |
| Explicit "remove access on refund" text | n/a | n/a | n/a | n/a | **Not verified.** Status `5` says revoked. No page fetched says "revoke access" verbatim. Older A-REFN says "take appropriate action". | 2026-10-11 |

### Apple: notification vs current state

| Fact | Source + quote | Checked |
|---|---|---|
| Payload is JWS-signed by the App Store | A-RECV: "signedPayload that's cryptographically signed by the App Store in JSON Web Signature (JWS) format" | 2026-10-11 |
| Verify with header and `x5c` chain to Apple root | A-SP: "Use the information in the decoded header to verify the signature." A-JWSH: `x5c` is leaf, Apple intermediate, Apple root (para.) | 2026-10-11 |
| Success response is HTTP 200-206 | A-RESP: "Send HTTP 200, or any HTTP code between 200 and 206" | 2026-10-11 |
| V2 retries: 5 times at 1, 12, 24, 48, 72 h | A-RESP: "retries five times, at 1, 12, 24, 48, and 72 hours" | 2026-10-11 |
| Sandbox sends once, no retries | A-RESP (para.): sandbox attempts one time only. | 2026-10-11 |
| Missed notifications: query the API | A-RESP: "you can always get up-to-date transaction information by calling App Store Server API" (para.: e.g., Get All Subscription Statuses, Get Notification History) | 2026-10-11 |
| Get All Subscription Statuses returns all groups | A-GASS: "Get the statuses for all of a customer's auto-renewable subscriptions in your app." | 2026-10-11 |
| Status enum 1-5 | A-STATUS: 1 active, 2 expired, 3 billing retry, 4 grace period, 5 revoked | 2026-10-11 |
| Apple says "notification is only a signal, always re-fetch" | **Not verified.** Apple says notifications carry signed data. Re-fetching is our design choice, backed by the outage guidance above. | 2026-10-11 |
| Ordering / duplicate guarantees | **Not verified.** No fetched Apple page states ordering or exactly-once delivery. | 2026-10-11 |

---

## 2. Google Play

| State | RTDN / `subscriptionState` | Access? | What ends it | Provider dates | Source + quote | Checked |
|---|---|---|---|---|---|---|
| New purchase | `SUBSCRIPTION_PURCHASED` (4); `SUBSCRIPTION_STATE_ACTIVE` | Yes | Renewal or lifecycle event | `expiryTime` | G-LC: "Give the user access to the content." Must acknowledge within 3 days or refund (para.). | 2026-10-11 |
| Renewed | `SUBSCRIPTION_RENEWED` (2); `..._ACTIVE` | Yes | Next event | New `expiryTime` | G-RTDN: "An active subscription was renewed." | 2026-10-11 |
| Silent grace | No RTDN; stays `..._ACTIVE` | Yes | Payment success or grace starts | Minimum 1 day | G-LC: "Play will wait a minimum of 1 day to ensure sufficient time for payment retries." | 2026-10-11 |
| Grace period | `SUBSCRIPTION_IN_GRACE_PERIOD` (6); `..._IN_GRACE_PERIOD` | Yes | Recovery or account hold | `expiryTime` extended dynamically | G-LC: "During a grace period, the user should still have access" | 2026-10-11 |
| Grace recovery | Handled as a renewal (para.) | Yes | n/a | Renewal date does **not** reset | G-LC: "When a subscription recovers during a grace period, the renewal date does not reset." | 2026-10-11 |
| Pre-hold retry window | No named state | Yes | Up to 48 h, then hold | n/a | G-LC: "additional attempts to charge the payment method for up to 48 hours" | 2026-10-11 |
| Account hold | `SUBSCRIPTION_ON_HOLD` (5); `..._ON_HOLD` | **No** | Recovery, or expiry | `expiryTime` set to the past; hold = 60 days minus grace by default | G-LC: "you should block access to the subscription entitlement." "60 days minus any grace period duration." | 2026-10-11 |
| Recovered | `SUBSCRIPTION_RECOVERED` (1); `..._ACTIVE` | Yes, restore | Next event | Future `expiryTime`; billing date moves to recovery date | G-LC: "you must then restore access to the subscribed content." Same purchase token (para.). | 2026-10-11 |
| Pause scheduled | `SUBSCRIPTION_PAUSE_SCHEDULE_CHANGED` (11); `..._ACTIVE` | Yes until next renewal | Pause starts at period end | Pause 1 week to 3 months (para.) | G-LC: "keep access to their subscription until the next renewal date." | 2026-10-11 |
| Paused | `SUBSCRIPTION_PAUSED` (10); `..._PAUSED` | **No** | Auto-resume or manual resume | `PausedStateContext` gives resume time | G-LC: "the user should lose access to their subscription" | 2026-10-11 |
| Resume from pause | `SUBSCRIPTION_RECOVERED` (1) | Yes | n/a | n/a | G-RTDN: "recovered from account hold or resumed from pause." Resume with failed payment goes to hold (para.). | 2026-10-11 |
| Deferred | `SUBSCRIPTION_DEFERRED` (9); `..._ACTIVE` | Yes, not charged | Deferred date reached | Renewal date updated | G-LC: "full access but is not charged." | 2026-10-11 |
| Canceled, not yet expired | `SUBSCRIPTION_CANCELED` (3); `..._CANCELED` | Yes until `expiryTime` | `expiryTime` passes, or restore | `expiryTime`; past date = lose now | G-LC: "contains the date when the user should lose access" | 2026-10-11 |
| Restarted (restore) | `SUBSCRIPTION_RESTARTED` (7); `..._ACTIVE` | Yes | Next event | Same purchase token; cancel fields cleared | G-RTDN: "canceled but had not expired yet when the user restores." | 2026-10-11 |
| Revoked | `SUBSCRIPTION_REVOKED` (12); state becomes `..._EXPIRED` | **No, immediately** | Terminal | n/a | G-LC: "revoke the user's entitlement immediately." Causes: API revoke or chargeback (para.). | 2026-10-11 |
| Expired | `SUBSCRIPTION_EXPIRED` (13); `..._EXPIRED` | No | Terminal; resubscribe = new token | Past `expiryTime` | G-LC: "Once a subscription expires, the user should lose access" | 2026-10-11 |

### Google: RTDN and identity

| Fact | Source + quote | Checked |
|---|---|---|
| RTDN is a change signal only | G-RTDN: "These notifications tell you only that the purchase state changed." | 2026-10-11 |
| Must call the API after RTDN | G-RTDN: "You must call the Google Play Developer API after receiving Real-time developer notifications" | 2026-10-11 |
| `subscriptionsv2.get` is the source of truth | G-LC: "considered the source of truth for subscription management." | 2026-10-11 |
| Deduplicate on `messageId` | G-RTDN (para.): check `messageId` uniqueness to avoid duplicate processing. | 2026-10-11 |
| `SubscriptionNotification` fields | G-RTDN: `version`, `notificationType`, `purchaseToken`. No `subscriptionId` documented. | 2026-10-11 |
| `linkedPurchaseToken` | G-LC: "indicates the old purchase from which the user upgraded, downgraded, or resubscribed." | 2026-10-11 |
| Resubscribe after full expiry has **no** `linkedPurchaseToken` | G-LC (para.): new token; link via `outOfAppPurchaseContext.expiredPurchaseToken` or obfuscated IDs. | 2026-10-11 |
| Ordering guarantees | **Not verified** on G-RTDN or G-LC. | 2026-10-11 |

---

## 3. Identity notes

| Question | Answer | Source + quote | Checked |
|---|---|---|---|
| Does `originalTransactionId` link renewals? | Yes | A-OID: "Identify one or more renewals for the same subscription." | 2026-10-11 |
| Store it as the subscription key? | Apple recommends it | A-OTID: "save the original transaction identifier to uniquely identify auto-renewable subscriptions." | 2026-10-11 |
| Is `transactionId` stable across renewals? | No. Each renewal is a new transaction | A-EXP (para.): renewal creates a new transaction. A-OID: `originalID` and `id` differ for renewals. | 2026-10-11 |
| Does `appAccountToken` link to an app account? | Yes, if the app sets it | A-TAAT: "A UUID that associates the transaction with a user on your own service." | 2026-10-11 |
| Is `appAccountToken` always present? | No. It is optional | A-AAT (para.): you can optionally generate it. Can be set later via Set App Account Token. | 2026-10-11 |
| Does Apple validate the token maps to a real account? | **Not verified.** Treat as client-supplied. | A-AAT does not say. | 2026-10-11 |
| Google equivalent | `purchaseToken`, `linkedPurchaseToken`, obfuscated account ID | G-LC (see table 2). | 2026-10-11 |
| Web purchase and App Store purchase = one transaction? | **No official basis found.** They are separate purchases with separate IDs. | No fetched page links a non-App-Store web purchase to an App Store transaction. "Outside of your app" in A-AAT means App Store purchases made outside the app (para.), not web checkout. | 2026-10-11 |

Modelling rule: link purchases to one **account**, not one **transaction**.
Cross-store entitlement merging is our own policy. Label it as such.

---

## 4. PostgreSQL row-lock notes

| Question | Answer | Source + quote | Checked |
|---|---|---|---|
| Lock a FK check takes on the referenced row | `FOR KEY SHARE` | PG-93: "foreign key checks use the new KEY SHARE lock mode" | 2026-10-11 |
| Is that stated on the v17 locking page? | **No.** v17 page defines modes only. FK-check mode is from 9.3 release notes. | PG-LOCK (para.); PG-93. | 2026-10-11 |
| Does `SELECT ... FOR UPDATE` on the parent block a child INSERT's FK check? | Yes | PG-LOCK Table 13.3: `FOR KEY SHARE` conflicts with `FOR UPDATE`. "`SELECT FOR UPDATE` is blocked, but not `SELECT FOR NO KEY UPDATE`." | 2026-10-11 |
| Does `FOR NO KEY UPDATE` avoid that block? | Yes | PG-LOCK: "this lock will not block `SELECT FOR KEY SHARE` commands" | 2026-10-11 |
| Which plain UPDATE takes which lock? | Non-key UPDATE takes `FOR NO KEY UPDATE`. Key-changing UPDATE or DELETE takes `FOR UPDATE`. | PG-LOCK: "acquired by any `UPDATE` that does not acquire a `FOR UPDATE` lock." | 2026-10-11 |
| What counts as a "key" column? | Columns with a unique index usable by a FK. Partial/expression indexes excluded | PG-LOCK (para.): may change in future. | 2026-10-11 |
| `FOR KEY SHARE` vs `FOR NO KEY UPDATE` conflict? | No conflict | PG-93: "which does not conflict with NO KEY UPDATE" | 2026-10-11 |

Practical rule: lock a parent row with `FOR NO KEY UPDATE` when you
will not change its key. Child inserts then proceed without waiting.
Deadlock-freedom is not guaranteed by this. Lock order still matters.

---

## 5. Commonly overclaimed statements

| Overclaim | Why wrong | Correct wording | Source |
|---|---|---|---|
| "Billing retry grants access." | Apple: no-subtype `DID_FAIL_TO_RENEW` lets you stop service. Google: account hold blocks access. | "Grace period grants access. Billing retry outside grace does not." | A-NT, A-ST, G-LC |
| "Grace period is always on." | Apple grace is opt-in in App Store Connect. Google grace is default but configurable. | "Grace applies only if configured. Read the provider's grace expiry date." | A-GP, G-LC |
| "Apple retry is 60 days after grace." | Apple says retry runs 60 days or until fixed/canceled. Grace start/end inside that window: **not verified**. | "Apple retries billing for up to 60 days." | A-NT, A-BILL |
| "Google account hold is always 30 days." | Default is 60 days minus grace. | "Hold length defaults to 60 days minus grace." | G-LC |
| "Canceled means no access." | Google canceled keeps access until `expiryTime`. | "Canceled stops renewal. Access lasts until expiry." | G-LC |
| "Paused means canceled." | Paused is its own state; auto-renew stays on. | "Paused: no access, resumes later." | G-LC |
| "The webhook payload is the current state." | Google: RTDN only signals a change. | "Fetch `subscriptionsv2.get` after each RTDN." | G-RTDN, G-LC |
| "Apple says notifications are only signals." | Apple sends signed data. Re-fetching is our choice. | "We re-fetch status as a design choice." | A-RECV, A-RESP |
| "Notifications arrive once and in order." | Apple retries up to 5 times. Google advises `messageId` dedupe. Ordering not documented. | "Deliveries may repeat. Handlers must be idempotent." | A-RESP, G-RTDN |
| "An outbox gives exactly-once delivery." | An outbox gives at-least-once publish. Consumers can see duplicates. Engineering reasoning, not a provider claim. | "Outbox plus idempotent consumers gives effectively-once effects." | Reasoning; dedupe guidance in G-RTDN |
| "`transactionId` identifies the subscription." | Each Apple renewal is a new transaction. | "Use `originalTransactionId` for the subscription." | A-OID, A-EXP |
| "`appAccountToken` proves account ownership." | It is optional, app-generated. Validation not documented. | "`appAccountToken` is an app-supplied hint." | A-AAT, A-TAAT |
| "Web purchase and App Store purchase are one transaction." | No official basis. | "Two purchases may map to one account by our policy." | Section 3 |
| "Every resubscribe has a `linkedPurchaseToken`." | Resubscribe after full expiry does not. | "Use `outOfAppPurchaseContext` for post-expiry resubscribe." | G-LC |
| "Revoked keeps access until expiry." | Google: revoke immediately. Apple status 5 = revoked. | "Revocation ends access now." | G-LC, A-STATUS |
| "A FK insert takes no lock on the parent." | It takes `FOR KEY SHARE` on the parent row. | "FK checks take `FOR KEY SHARE`; parent `FOR UPDATE` blocks them." | PG-93, PG-LOCK |
| "`FOR NO KEY UPDATE` prevents all conflicts." | It still conflicts with `FOR SHARE` and stronger. | "It only avoids blocking `FOR KEY SHARE`." | PG-LOCK |

---

## Not verified

- Apple explicit "revoke access on refund" wording (status `5` implies it).
- Apple access-until-expiry wording for `AUTO_RENEW_DISABLED`.
- Apple notification ordering or duplicate guarantees; `notificationUUID` dedupe wording.
- How Apple grace days count inside the 60-day retry window.
- Google RTDN ordering guarantees.
- Google RTDN name for recovery inside grace (docs say "handle the renewal").
- Any official cross-store (web + App Store) transaction identity.
- Whether Apple validates `appAccountToken` against an account.
