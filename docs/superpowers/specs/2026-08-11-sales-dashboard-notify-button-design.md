# Sales Dashboard "Notify Site Updated" Button — Design Spec

**Date:** 2026-08-11  
**Feature:** Add a button to the FMB Site Tracker (sales dashboard) that sends an SMS to a customer notifying them their demo website has been updated and is ready to view.

## Overview

When a sales team member marks a site as "Won", "Sent", or updates its status in the tracker, they need a quick way to notify the customer that their site is live and ready. This feature adds a **Notify** button to each site row that:

1. Looks up the customer's phone number by matching the site's demo URL against the customer registry
2. Shows a confirmation dialog with the destination phone and business name
3. Sends an SMS via Twilio with the site update notification
4. Provides inline feedback (success message or error alert) after sending

## Architecture

### 1. Core Helper: `voice-agent/agent.py` — Reusable SMS Helper

Extract a reusable `send_sms(to: str, body: str) -> dict` function from the existing `_twilio()` singleton pattern.

**Signature:**
```python
def send_sms(to: str, body: str) -> dict[str, Any]:
    """
    Send an SMS via Twilio.
    Args:
        to: Destination phone number (E.164 format preferred)
        body: SMS message body (string)
    Returns:
        {"ok": True, "sid": "<message_sid>"} on success
        {"ok": False, "error": "<message>"} on failure
    """
```

**Implementation:** Reuse the `_twilio()` singleton client and existing error handling. Log all sends and failures to the application log.

---

### 2. Voice Agent Endpoint: `voice-agent/server.py`

**New endpoint:** `POST /api/customers/{phone}/notify-site-updated`

**Purpose:** Accept a phone number, look up the associated customer, and send them an SMS notification that their site is ready.

**Request:**
- Path param: `phone` — customer phone in any format (will normalize via `customers.normalize_phone()`)

**Response on success (200):**
```json
{
  "ok": true,
  "message": "Notification sent to {phone}",
  "customer": { "phone": "...", "business_name": "...", "demo_url": "..." }
}
```

**Response on error:**
- `404`: Customer not found by phone
- `400`: Customer found but no `demo_url` on file (site not yet assigned/ready)
- `500`: Twilio send failed (includes error message)

**SMS Body Template:**

```
Hi from {OWNER_NAME} — your website for {business_name} has been updated and is ready to view:

{demo_url}

Questions? Reply here or call {OWNER_CALLBACK_NUMBER}.
```

Environment variables used: `OWNER_NAME`, `OWNER_CALLBACK_NUMBER`, `TWILIO_*` (existing).

**Implementation Notes:**
- Reuse the `send_sms()` helper from agent.py
- Look up customer via `customers.get(phone)`; return 404 if not found
- Validate `customer["demo_url"]` is present; return 400 if missing
- Build the SMS body from config and customer data
- Log the send attempt (success/failure) with phone and customer name

---

### 3. Site Tracker Backend: `site-tracker/app.py`

**Updates to existing endpoints:**

1. **`/api/sites`** — Add a `phone` field to each site object.
   - Build a `demo_url -> phone` index from `_load_customers_rows()` each time the list is fetched.
   - Match each site's `url` (e.g., `https://floridamanweb.online/some-hash/`) against customer `demo_url`.
   - Populate `phone: null` if no match found.

2. **`/api/sites/{h}`** — Include `phone` in the site detail response (same lookup as above).

**New endpoint:** `POST /api/sites/{h}/notify`

**Purpose:** User clicks "Notify" on a specific site row. This endpoint:
1. Validates the site exists (404 if not)
2. Validates a phone was found for the site (400 if missing)
3. Proxies to `CUSTOMERS_API + /api/customers/{phone}/notify-site-updated` with the exact same phone number
4. Returns the proxied response as-is

**Response:**
- Passes through from voice-agent endpoint: `{"ok": true, ...}` on success
- `404` if site hash not found
- `400` if no phone on file for this site
- `500` if voice-agent endpoint fails (proxied error)

**Implementation Notes:**
- Follow the existing pattern from `_load_customers_rows()` for proxying to `CUSTOMERS_API`
- Use the same error handling: catch `URLError`, `TimeoutError`, etc., and surface as HTTP 500

---

### 4. Frontend: `site-tracker/static/index.html`

**UI Changes:**

1. Add a "Notify" column header to the table (between "Live site" and "Status").
2. For each site row:
   - If `s.phone` is present: show an enabled button labeled "📱 Notify"
   - If `s.phone` is `null`: show a disabled button `title="no customer phone on file"`
3. Button click triggers: `notifySiteUpdated(hash, phone, businessName)`

**Interaction Flow:**

```javascript
window.notifySiteUpdated = async (hash, phone, businessName) => {
  // 1. Confirm with user
  const confirmed = confirm(
    `Text ${businessName} at ${phone} that their site is updated and ready to view?`
  );
  if (!confirmed) return;

  // 2. Disable button and show pending state
  const btn = document.querySelector(`button[data-h="${hash}"]`);
  btn.disabled = true;
  btn.textContent = "📤 Sending…";

  // 3. POST to site-tracker endpoint
  try {
    const res = await fetch(`/api/sites/${hash}/notify`, { method: "POST" });
    if (!res.ok) {
      const err = await res.text();
      throw new Error(err);
    }
    // 4. Show success message
    btn.textContent = "✓ Sent";
    btn.style.background = "#2a8c4e"; // green tint
    setTimeout(() => {
      btn.textContent = "📱 Notify";
      btn.style.background = "";
    }, 3000);
  } catch (e) {
    // 5. Show error and re-enable
    alert(`Failed to send notification: ${e.message}`);
    btn.disabled = false;
    btn.textContent = "📱 Notify";
  }
};
```

**CSS:** Disabled button styling (grey background, reduced opacity).

---

## Error Handling & Edge Cases

| Scenario | Behavior |
|----------|----------|
| Site exists, customer found, SMS sent | ✓ Success message, button shows "✓ Sent" for 3s |
| Site hash not found | 404 from site-tracker; alert user |
| Site found but phone not found (customer not in registry) | Disabled button shows "no customer phone on file" tooltip |
| Customer found but demo_url missing (site not assigned to customer yet) | 400 from voice-agent; alert "site not yet ready, missing demo_url" |
| Twilio API failure (quota, network, etc.) | 500 from voice-agent; alert the Twilio error message; button re-enables |
| CUSTOMERS_API unreachable | 500 from site-tracker; alert "could not reach customer registry" |

No automatic retry. User can click again after resolving the underlying issue.

---

## Testing

### Unit Tests

1. **`test_send_sms()` in `voice-agent/tests/`**
   - Mock the Twilio client
   - Test success: returns `{"ok": true, "sid": "..."}`
   - Test failure: catches exceptions and returns `{"ok": false, "error": "..."}`

2. **`test_notify_site_updated()` in `voice-agent/tests/`**
   - Mock `customers.get()`, `send_sms()`
   - Test 404: customer not found
   - Test 400: customer has no demo_url
   - Test 200: success response with customer data

3. **`test_sites_notify_endpoint()` in `site-tracker/tests/` (if it exists, else add)**
   - Mock `_load_customers_rows()`, the voice-agent proxy call
   - Test 404: site hash not found
   - Test 400: no matched phone
   - Test 200: proxy returns success

### Manual Testing

1. Open site-tracker (floridamanweb.online/desk)
2. Verify a row with a matched customer shows an enabled "Notify" button
3. Click the button, confirm the dialog (or cancel)
4. On confirm, watch the button show "📤 Sending…" briefly, then "✓ Sent"
5. Check the customer's real SMS inbox (or Twilio logs) for the notification
6. Test error paths: disable CUSTOMERS_API, trigger a 400/404/500

---

## Deployment & Configuration

No new environment variables required. Uses existing `OWNER_NAME`, `OWNER_CALLBACK_NUMBER`, `TWILIO_*` from voice-agent config.

Site-tracker needs `CUSTOMERS_API` (already required) to reach the voice-agent for lookups and proxying.

---

## Success Criteria

- [x] Approved design by user
- [ ] All code written and unit tests passing
- [ ] Manual testing on a real customer row confirms SMS delivery
- [ ] Button gracefully handles missing customer/phone/demo_url cases
- [ ] Implementation follows existing codebase patterns (error handling, logging, API structure)

---

## Out of Scope

- Editing or resending SMS templates per-customer
- Scheduling notifications for a future time
- SMS opt-out / do-not-contact list integration (TCPA compliance managed elsewhere)
- Analytics on notification open rates or responses
- Bulk notification to all customers in a status (e.g., "notify all 'Won' customers")

These are future enhancements; the initial feature is a per-row, manual notify button.
