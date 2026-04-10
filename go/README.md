# Drip SDK (Go)

This directory contains the Go client for the public Drip API.

It is intentionally small right now and focuses on the core integration path:

- `Ping`
- `CreateCustomer`
- `Charge`
- `TrackUsage`

## Install

```bash
go get github.com/DripYCx26/drip-sdk/go
```

## Quickstart

```go
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

	_, err = client.Charge(context.Background(), drip.ChargeParams{
		CustomerID: customer.ID,
		Meter:      "api_calls",
		Quantity:   1,
	})
	if err != nil {
		log.Fatal(err)
	}
}
```

`NewClient(drip.Config{})` reads `DRIP_API_KEY` from the environment automatically.

## Environment

```bash
export DRIP_API_KEY=sk_test_...
```

Optional:

```bash
export DRIP_API_URL=https://api.drippay.dev/v1
```

## Example

See [`examples/charge/main.go`](./examples/charge/main.go).
