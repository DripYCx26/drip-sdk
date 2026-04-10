from drip import drip


def main() -> None:
    customer = drip.create_customer(external_customer_id="user_123")

    drip.charge(
        customer_id=customer.id,
        meter="tokens",
        quantity=1847,
    )


if __name__ == "__main__":
    main()
