package main

import (
	"context"
	"log"

	drip "github.com/DripYCx26/drip-sdk/go"
)

func main() {
	client, err := drip.NewClient(drip.Config{})
	if err != nil {
		log.Fatal(err)
	}

	customer, err := client.CreateCustomer(context.Background(), drip.CreateCustomerParams{
		ExternalCustomerID: "user_123",
	})
	if err != nil {
		log.Fatal(err)
	}

	result, err := client.Charge(context.Background(), drip.ChargeParams{
		CustomerID: customer.ID,
		Meter:      "tokens",
		Quantity:   1847,
	})
	if err != nil {
		log.Fatal(err)
	}

	log.Printf("charge %s status=%s", result.Charge.ID, result.Charge.Status)
}
