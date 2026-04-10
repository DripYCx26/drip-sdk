package drip

import "fmt"

// APIError represents a non-2xx response from the Drip API.
type APIError struct {
	StatusCode int
	Code       string
	Message    string
	Body       string
}

func (e *APIError) Error() string {
	if e == nil {
		return ""
	}

	if e.Code != "" {
		return fmt.Sprintf("drip API error (%d %s): %s", e.StatusCode, e.Code, e.Message)
	}

	return fmt.Sprintf("drip API error (%d): %s", e.StatusCode, e.Message)
}
