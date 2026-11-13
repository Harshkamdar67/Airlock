---
type: llm
weight: 2
---

The answer explains that Stripe signs each webhook with the endpoint's signing secret, sends the signature in the Stripe-Signature header with a timestamp, that verification computes an HMAC-SHA256 over the timestamp and raw payload, that the official libraries apply a default tolerance of five minutes (300 seconds) to reject replayed events, and it cites Stripe documentation.
