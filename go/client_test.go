package drip

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestCreateCustomer(t *testing.T) {
	t.Parallel()

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/customers" {
			t.Fatalf("unexpected path: %s", r.URL.Path)
		}
		if got := r.Header.Get("Authorization"); got != "Bearer sk_test_123" {
			t.Fatalf("unexpected auth header: %s", got)
		}

		var payload map[string]any
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Fatal(err)
		}
		if payload["externalCustomerId"] != "user_123" {
			t.Fatalf("unexpected external customer id: %#v", payload["externalCustomerId"])
		}

		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"id":                 "cus_123",
			"externalCustomerId": "user_123",
			"status":             "ACTIVE",
		})
	}))
	defer server.Close()

	client, err := NewClient(Config{
		APIKey:  "sk_test_123",
		BaseURL: server.URL + "/v1",
	})
	if err != nil {
		t.Fatal(err)
	}

	customer, err := client.CreateCustomer(context.Background(), CreateCustomerParams{
		ExternalCustomerID: "user_123",
	})
	if err != nil {
		t.Fatal(err)
	}

	if customer.ID != "cus_123" {
		t.Fatalf("unexpected customer id: %s", customer.ID)
	}
}

func TestChargeUsesUsageEndpoint(t *testing.T) {
	t.Parallel()

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/usage" {
			t.Fatalf("unexpected path: %s", r.URL.Path)
		}

		var payload map[string]any
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Fatal(err)
		}
		if payload["usageType"] != "tokens" {
			t.Fatalf("unexpected usageType: %#v", payload["usageType"])
		}
		if payload["customerId"] != "cus_123" {
			t.Fatalf("unexpected customerId: %#v", payload["customerId"])
		}
		if payload["idempotencyKey"] == "" {
			t.Fatal("missing idempotencyKey")
		}

		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"success":      true,
			"usageEventId": "usage_123",
			"charge": map[string]any{
				"id":         "chg_123",
				"amountUsdc": "0.001000",
				"status":     "CONFIRMED",
			},
		})
	}))
	defer server.Close()

	client, err := NewClient(Config{
		APIKey:  "sk_test_123",
		BaseURL: server.URL + "/v1",
	})
	if err != nil {
		t.Fatal(err)
	}

	result, err := client.Charge(context.Background(), ChargeParams{
		CustomerID: "cus_123",
		Meter:      "tokens",
		Quantity:   1847,
	})
	if err != nil {
		t.Fatal(err)
	}

	if !result.Success {
		t.Fatal("expected success")
	}
	if result.Charge.ID != "chg_123" {
		t.Fatalf("unexpected charge id: %s", result.Charge.ID)
	}
}
