package drip

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"
)

const defaultBaseURL = "https://api.drippay.dev/v1"

// HTTPClient is the subset of *http.Client used by the SDK.
type HTTPClient interface {
	Do(req *http.Request) (*http.Response, error)
}

// Client is the Drip Go SDK client.
type Client struct {
	apiKey     string
	baseURL    string
	httpClient HTTPClient
}

type apiErrorResponse struct {
	Error   string `json:"error"`
	Message string `json:"message"`
	Code    string `json:"code"`
}

// NewClient creates a Drip client. If APIKey or BaseURL are empty, it falls back
// to DRIP_API_KEY and DRIP_API_URL/DRIP_BASE_URL from the environment.
func NewClient(cfg Config) (*Client, error) {
	apiKey := cfg.APIKey
	if apiKey == "" {
		apiKey = os.Getenv("DRIP_API_KEY")
	}
	if apiKey == "" {
		return nil, errors.New("drip API key is required: set DRIP_API_KEY or pass Config.APIKey")
	}

	baseURL := cfg.BaseURL
	if baseURL == "" {
		baseURL = os.Getenv("DRIP_API_URL")
	}
	if baseURL == "" {
		baseURL = os.Getenv("DRIP_BASE_URL")
	}
	if baseURL == "" {
		baseURL = defaultBaseURL
	}

	httpClient := cfg.HTTPClient
	if httpClient == nil {
		timeout := cfg.Timeout
		if timeout <= 0 {
			timeout = 30 * time.Second
		}
		httpClient = &http.Client{Timeout: timeout}
	}

	return &Client{
		apiKey:     apiKey,
		baseURL:    strings.TrimRight(baseURL, "/"),
		httpClient: httpClient,
	}, nil
}

// Ping checks connectivity to the Drip health endpoint.
func (c *Client) Ping(ctx context.Context) (*HealthResponse, error) {
	start := time.Now()
	url := c.healthURL()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+c.apiKey)

	res, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer res.Body.Close()

	if res.StatusCode < 200 || res.StatusCode >= 300 {
		return nil, c.buildAPIError(res)
	}

	var out struct {
		Status    string `json:"status"`
		Timestamp int64  `json:"timestamp"`
	}
	if err := json.NewDecoder(res.Body).Decode(&out); err != nil {
		return nil, err
	}

	return &HealthResponse{
		OK:        out.Status == "healthy",
		Status:    out.Status,
		LatencyMS: time.Since(start).Milliseconds(),
		Timestamp: out.Timestamp,
	}, nil
}

// CreateCustomer creates a public Drip customer.
func (c *Client) CreateCustomer(ctx context.Context, params CreateCustomerParams) (*Customer, error) {
	var out Customer
	if err := c.doJSON(ctx, http.MethodPost, "/customers", params, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// Charge records usage and creates a billable charge.
func (c *Client) Charge(ctx context.Context, params ChargeParams) (*ChargeResult, error) {
	payload := map[string]any{
		"customerId":     params.CustomerID,
		"usageType":      params.Meter,
		"quantity":       params.Quantity,
		"idempotencyKey": withDefaultIdempotencyKey("chg", params.IdempotencyKey),
	}
	if params.Metadata != nil {
		payload["metadata"] = params.Metadata
	}

	var out ChargeResult
	if err := c.doJSON(ctx, http.MethodPost, "/usage", payload, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// TrackUsage records usage without billing.
func (c *Client) TrackUsage(ctx context.Context, params TrackUsageParams) (*TrackUsageResult, error) {
	payload := map[string]any{
		"customerId":     params.CustomerID,
		"usageType":      params.Meter,
		"quantity":       params.Quantity,
		"idempotencyKey": withDefaultIdempotencyKey("track", params.IdempotencyKey),
	}
	if params.Units != "" {
		payload["units"] = params.Units
	}
	if params.Description != "" {
		payload["description"] = params.Description
	}
	if params.Metadata != nil {
		payload["metadata"] = params.Metadata
	}

	var out TrackUsageResult
	if err := c.doJSON(ctx, http.MethodPost, "/usage/internal", payload, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

func (c *Client) doJSON(ctx context.Context, method string, path string, body any, out any) error {
	var requestBody io.Reader
	if body != nil {
		payload, err := json.Marshal(body)
		if err != nil {
			return err
		}
		requestBody = bytes.NewReader(payload)
	}

	req, err := http.NewRequestWithContext(ctx, method, c.baseURL+path, requestBody)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+c.apiKey)
	req.Header.Set("Content-Type", "application/json")

	res, err := c.httpClient.Do(req)
	if err != nil {
		return err
	}
	defer res.Body.Close()

	if res.StatusCode < 200 || res.StatusCode >= 300 {
		return c.buildAPIError(res)
	}

	if out == nil || res.StatusCode == http.StatusNoContent {
		return nil
	}

	return json.NewDecoder(res.Body).Decode(out)
}

func (c *Client) buildAPIError(res *http.Response) error {
	body, _ := io.ReadAll(res.Body)

	var parsed apiErrorResponse
	if err := json.Unmarshal(body, &parsed); err == nil {
		message := parsed.Message
		if message == "" {
			message = parsed.Error
		}
		if message != "" {
			return &APIError{
				StatusCode: res.StatusCode,
				Code:       parsed.Code,
				Message:    message,
				Body:       string(body),
			}
		}
	}

	return &APIError{
		StatusCode: res.StatusCode,
		Message:    http.StatusText(res.StatusCode),
		Body:       string(body),
	}
}

func (c *Client) healthURL() string {
	trimmed := strings.TrimRight(c.baseURL, "/")
	trimmed = strings.TrimSuffix(trimmed, "/v1")
	return trimmed + "/health"
}

func withDefaultIdempotencyKey(prefix string, value string) string {
	if value != "" {
		return value
	}
	return generateIdempotencyKey(prefix)
}

func generateIdempotencyKey(prefix string) string {
	buf := make([]byte, 12)
	if _, err := rand.Read(buf); err != nil {
		return fmt.Sprintf("%s_%d", prefix, time.Now().UnixNano())
	}
	return prefix + "_" + hex.EncodeToString(buf)
}
