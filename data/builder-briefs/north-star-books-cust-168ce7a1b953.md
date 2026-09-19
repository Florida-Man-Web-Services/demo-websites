# Website build brief

- customer_id: cust-168ce7a1b953
- phone: +13555550100
- business: North Star Books
- contact: 
- email: 
- category: 
- status: requirements_ready
- generated_at: 2026-09-14T23:26:30+00:00

## Summary

A local bookstore site.

## Structured requirements

```json
{
  "business_name": "North Star Books",
  "audience": "Local readers",
  "goal": "Bring people into the shop",
  "must_haves": [
    "hours",
    "contact"
  ],
  "follow_up": "Text the demo"
}
```

## Agent instructions

1. Create or update `generated-sites/north-star-books.html` following demo-websites landing rules
   (NAP truth, no invented phone/hours, self-contained HTML).
2. Match FMWS craft rubric; mobile-first; Hours/Address/tel hooks for owner_updates.
3. When done, call mark_demo_ready API / customers.mark_demo_ready with the live URL.
4. Do not invent NAP; leave placeholders only if the interview did not collect them.
