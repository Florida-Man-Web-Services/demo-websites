# Website build brief

- customer_id: cust-763c3cd59f16
- phone: +13555550102
- business: X Cafe
- contact: 
- email: 
- category: 
- status: requirements_ready
- generated_at: 2026-08-11T15:43:42+00:00

## Summary

Simple cafe site

## Structured requirements

```json
{
  "goals": [
    "bookings"
  ],
  "pages": [
    "home",
    "contact"
  ]
}
```

## Agent instructions

1. Create or update `generated-sites/x-cafe.html` following demo-websites landing rules
   (NAP truth, no invented phone/hours, self-contained HTML).
2. Match FMWS craft rubric; mobile-first; Hours/Address/tel hooks for owner_updates.
3. When done, call mark_demo_ready API / customers.mark_demo_ready with the live URL.
4. Do not invent NAP; leave placeholders only if the interview did not collect them.
