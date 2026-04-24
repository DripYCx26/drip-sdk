#!/usr/bin/env python3
"""
Sample: Charge customers with the Drip Python SDK.

Just pass user= with your user ID. Drip handles customer creation,
smart account provisioning, and billing — all in one call.

Prerequisites:
  - Backend running: pnpm dev:backend
  - pip install drip-sdk
  - Set DRIP_API_KEY in your environment (or pass it directly)

Usage:
  DRIP_API_KEY=sk_test_... python examples/provision_and_charge.py

Or with a custom API URL:
  DRIP_API_KEY=sk_test_... DRIP_API_URL=http://localhost:3001/v1 python examples/provision_and_charge.py
"""

from drip import Drip

drip = Drip()  # reads DRIP_API_KEY and DRIP_API_URL from env


def main() -> None:
    user_id = f"demo-python-{__import__('time').time_ns()}"

    # One line — customer auto-created + smart account auto-provisioned
    print("1. Charging for 1000 tokens...")
    charge = drip.charge(user=user_id, meter="tokens", quantity=1000)
    print(f"   Charge ID: {charge.charge.id}")
    print(f"   Amount: {charge.charge.amount_usdc} USDC")

    # Subsequent calls hit the cache — no extra API calls
    print("\n2. Charging for 5 API calls...")
    charge2 = drip.charge(user=user_id, meter="api_calls", quantity=5)
    print(f"   Charge ID: {charge2.charge.id}")
    print(f"   Amount: {charge2.charge.amount_usdc} USDC")

    # Use a run context manager for multi-step workflows
    print("\n3. Running a workflow...")
    with drip.run(user=user_id, workflow="demo-pipeline") as run:
        run.event("step.started", 1)
        run.charge("compute_seconds", 2.5)
        run.event("step.completed", 1)
    print("   Run completed!")

    print("\nDone! Settlement triggers automatically when charges accumulate.")


if __name__ == "__main__":
    main()
