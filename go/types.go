package drip

import "time"

// Config configures a Drip client.
type Config struct {
	APIKey     string
	BaseURL    string
	Timeout    time.Duration
	HTTPClient HTTPClient
}

// HealthResponse is returned from Ping.
type HealthResponse struct {
	OK        bool   `json:"ok"`
	Status    string `json:"status"`
	LatencyMS int64  `json:"latencyMs"`
	Timestamp int64  `json:"timestamp"`
}

// CreateCustomerParams creates a Drip customer.
type CreateCustomerParams struct {
	ExternalCustomerID string         `json:"externalCustomerId,omitempty"`
	OnchainAddress     string         `json:"onchainAddress,omitempty"`
	Metadata           map[string]any `json:"metadata,omitempty"`
}

// Customer is the public customer object returned by the API.
type Customer struct {
	ID                 string         `json:"id"`
	BusinessID         string         `json:"businessId,omitempty"`
	ExternalCustomerID string         `json:"externalCustomerId,omitempty"`
	OnchainAddress     string         `json:"onchainAddress,omitempty"`
	Metadata           map[string]any `json:"metadata,omitempty"`
	Status             string         `json:"status,omitempty"`
	CreatedAt          string         `json:"createdAt,omitempty"`
	UpdatedAt          string         `json:"updatedAt,omitempty"`
}

// ChargeParams records usage and creates a billable charge.
type ChargeParams struct {
	CustomerID     string         `json:"customerId"`
	Meter          string         `json:"-"`
	Quantity       float64        `json:"quantity"`
	IdempotencyKey string         `json:"idempotencyKey,omitempty"`
	Metadata       map[string]any `json:"metadata,omitempty"`
}

// TrackUsageParams records internal usage without billing.
type TrackUsageParams struct {
	CustomerID     string         `json:"customerId"`
	Meter          string         `json:"-"`
	Quantity       float64        `json:"quantity"`
	IdempotencyKey string         `json:"idempotencyKey,omitempty"`
	Units          string         `json:"units,omitempty"`
	Description    string         `json:"description,omitempty"`
	Metadata       map[string]any `json:"metadata,omitempty"`
}

// Charge captures a single charge result.
type Charge struct {
	ID          string `json:"id"`
	AmountUSDC  string `json:"amountUsdc,omitempty"`
	AmountToken string `json:"amountToken,omitempty"`
	TxHash      string `json:"txHash,omitempty"`
	Status      string `json:"status,omitempty"`
}

// ChargeResult is returned from Charge.
type ChargeResult struct {
	Success      bool   `json:"success"`
	UsageEventID string `json:"usageEventId,omitempty"`
	IsDuplicate  bool   `json:"isDuplicate,omitempty"`
	Charge       Charge `json:"charge"`
}

// TrackUsageResult is returned from TrackUsage.
type TrackUsageResult struct {
	Success         bool   `json:"success"`
	UsageEventID    string `json:"usageEventId,omitempty"`
	IdempotencyKey  string `json:"idempotencyKey,omitempty"`
	PendingEvents   int    `json:"pendingEvents,omitempty"`
	IsDuplicate     bool   `json:"isDuplicate,omitempty"`
	NormalizedUnits string `json:"normalizedUnits,omitempty"`
}
