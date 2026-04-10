#!/usr/bin/env python3
"""
Live billing test — run this to see charges appear on drippay.dev dashboard.

Usage:
  DRIP_API_KEY=sk_live_... python examples/test_live_billing.py

Or with a custom API URL:
  DRIP_API_KEY=sk_live_... DRIP_API_URL=http://localhost:3000/v1 python examples/test_live_billing.py
"""

import time
from drip import Drip

drip = Drip()  # reads DRIP_API_KEY and DRIP_API_URL from env

USER = f"live-test-{int(time.time())}"


def main() -> None:
    print(f"API: {drip.config.base_url}")
    print(f"User: {USER}")
    print()

    # ── 1. Health check ──────────────────────────────────────────────
    print("1. Pinging API...")
    health = drip.ping()
    print(f"   Status: {health['status']}  Latency: {health['latency_ms']}ms")
    print()

    # ── 2. Create customer (auto-provisions smart account) ───────────
    print("2. Creating customer...")
    customer = drip.get_or_create_customer(USER)
    print(f"   Customer ID:    {customer.id}")
    print(f"   External ID:    {customer.external_customer_id}")
    print(f"   On-chain addr:  {customer.onchain_address or '(none — needs provisioning on this server)'}")
    print()

    has_onchain = customer.onchain_address is not None

    # ── 3. Track usage (always works, no on-chain needed) ────────────
    print("3. Tracking usage (non-billing)...")
    for meter, qty in [("api_calls", 5), ("tokens", 1200), ("compute_seconds", 3.7)]:
        result = drip.track_usage(user=USER, meter=meter, quantity=qty)
        print(f"   {meter}: {qty} → event {result.usage_event_id}")
    print()

    # ── 4. Charge (requires on-chain address) ────────────────────────
    if has_onchain:
        print("4. Charging (billing)...")
        for meter, qty in [("api_calls", 10), ("tokens", 5000)]:
            charge = drip.charge(user=USER, meter=meter, quantity=qty)
            print(f"   {meter}: {qty} → charge {charge.charge.id}  {charge.charge.amount_usdc} USDC  [{charge.charge.status.value}]")
    else:
        print("4. Skipping charge — no on-chain address.")
        print("   Deploy this branch to production to enable auto-provisioning,")
        print("   or run against local backend: DRIP_API_URL=http://localhost:3000/v1")
    print()

    # ── 5. Run context manager ───────────────────────────────────────
    print("5. Recording a run (workflow tracking)...")
    try:
        customer_id = drip._resolve_customer(USER)
        result = drip.record_run(
            customer_id=customer_id,
            workflow="sdk-live-test",
            events=[
                {"event_type": "request.received", "quantity": 1},
                {"event_type": "tokens.processed", "quantity": 2500},
                {"event_type": "response.sent", "quantity": 1},
            ],
            status="COMPLETED",
        )
        print(f"   Run: {result.run.id}")
        print(f"   Events created: {result.events.created}")
        print(f"   Summary: {result.summary}")
    except Exception as e:
        print(f"   Run failed: {e}")
    print()

    # ── 6. List customers to verify ──────────────────────────────────
    print("6. Listing recent customers...")
    customers = drip.list_customers(limit=5)
    for c in customers.data:
        print(f"   {c.id}  ext={c.external_customer_id or '—'}  addr={c.onchain_address or '—'}")
    print()

    print("Done! Check drippay.dev dashboard to see:")
    print(f"  - Customer '{USER}' in Customers tab")
    print(f"  - Usage events in Analytics")
    print(f"  - Run in Agent Runs")


if __name__ == "__main__":
    main()
