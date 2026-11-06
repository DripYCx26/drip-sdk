# Drip SDK (cURL)

This directory contains direct HTTP examples for the public Drip API.

Use these when you do not want a language SDK yet and just need working requests
that map cleanly onto the public API surface.

## Environment

```bash
export DRIP_API_KEY=sk_test_...
export DRIP_API_BASE=https://api.drippay.dev/v1
```

## Examples

- [`examples/create_customer.sh`](./examples/create_customer.sh)
- [`examples/track_usage.sh`](./examples/track_usage.sh)
- [`examples/charge_usage.sh`](./examples/charge_usage.sh)

## Flow

1. Create a customer with `POST /customers`
2. Use the returned `id` as `customerId`
3. Record billable usage with `POST /usage`
4. Or record non-billing usage with `POST /usage/internal`
