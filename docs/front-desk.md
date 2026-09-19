# Hosted-business front desk (wave 1)

Dedicated receptionist runtime for **one** published CMS tenant. Default **off**.

```bash
export FRONT_DESK_ENABLED=true
export FRONT_DESK_TENANT_SLUG=cool-cafe
export AGENT_MODE=front_desk
export BUSINESS_CMS_ENABLED=true
export BUSINESS_CMS_DATA_DIR=/tmp/fmws-cms
```

`AGENT_MODE=front_desk` fails closed without the slug and feature flag.

The public Gainesville 411 greeting and `customers.resolve_mode` paid→owner_updates mapping are unchanged. Front desk is an explicit runtime, not CID routing.

Tools (model cannot pass tenant/contact):

- `front_desk_get_business`
- `front_desk_leave_message`
- `front_desk_request_appointment` (request, not a booking)
- `front_desk_request_owner_callback` (not a live transfer)

Emergencies still go to 911. No live telephony transfer or owner SMS in this wave.
