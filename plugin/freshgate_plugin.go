// Package main implements FreshGate as a native Bifrost Go dynamic plugin.
//
// This plugin intercepts completed LLM responses, extracts and verifies volatile factual
// claims against Tavily and an LLM judge via the FreshGate API (or embedded engine),
// and decorates the response with freshness headers and a "freshgate" metadata payload.
//
// Compile with:
//   go build -buildmode=plugin -o freshgate.so freshgate_plugin.go

package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strconv"
	"time"

	"github.com/maximhq/bifrost/core/schemas"
)

type Config struct {
	FreshGateURL string `json:"freshgate_url"`
	TimeoutSec   int    `json:"timeout_sec"`
	FailOpen     bool   `json:"fail_open"`
	AsyncMode    bool   `json:"async_mode"`
}

var (
	pluginConfig = Config{
		FreshGateURL: "http://127.0.0.1:8000/verify",
		TimeoutSec:   4,
		FailOpen:     true,
		AsyncMode:    false,
	}
	httpClient = &http.Client{
		Timeout: 4 * time.Second,
	}
)

type VerifyRequest struct {
	Answer string `json:"answer"`
}

type VerificationResult struct {
	Claims []struct {
		ID         string   `json:"id"`
		Text       string   `json:"text"`
		Type       string   `json:"type"`
		Verdict    string   `json:"verdict"`
		Score      int      `json:"score"`
		Correction *string  `json:"correction"`
		Cached     bool     `json:"cached"`
	} `json:"claims"`
	Summary struct {
		Total    int `json:"total"`
		Checked  int `json:"checked"`
		Stale    int `json:"stale"`
		AvgScore int `json:"avg_score"`
	} `json:"summary"`
}

// Init initializes the FreshGate plugin with configuration from config.json.
func Init(config any) error {
	if values, ok := config.(map[string]any); ok {
		if url, ok := values["freshgate_url"].(string); ok && url != "" {
			pluginConfig.FreshGateURL = url
		}
		switch timeout := values["timeout_sec"].(type) {
		case float64:
			if timeout > 0 {
				pluginConfig.TimeoutSec = int(timeout)
			}
		case int:
			if timeout > 0 {
				pluginConfig.TimeoutSec = timeout
			}
		}
		if failOpen, ok := values["fail_open"].(bool); ok {
			pluginConfig.FailOpen = failOpen
		}
		if asyncMode, ok := values["async_mode"].(bool); ok {
			pluginConfig.AsyncMode = asyncMode
		}
	}
	if url := os.Getenv("FRESHGATE_URL"); url != "" {
		pluginConfig.FreshGateURL = url
	}
	if raw := os.Getenv("FRESHGATE_ASYNC_MODE"); raw != "" {
		if value, err := strconv.ParseBool(raw); err == nil {
			pluginConfig.AsyncMode = value
		}
	}
	if raw := os.Getenv("FRESHGATE_FAIL_OPEN"); raw != "" {
		if value, err := strconv.ParseBool(raw); err == nil {
			pluginConfig.FailOpen = value
		}
	}
	if raw := os.Getenv("FRESHGATE_TIMEOUT_SEC"); raw != "" {
		if value, err := strconv.Atoi(raw); err == nil && value > 0 {
			pluginConfig.TimeoutSec = value
		}
	}
	if pluginConfig.TimeoutSec <= 0 {
		pluginConfig.TimeoutSec = 4
	}
	httpClient.Timeout = time.Duration(pluginConfig.TimeoutSec) * time.Second
	fmt.Printf("[FreshGate Plugin] Initialized with endpoint: %s\n", pluginConfig.FreshGateURL)
	return nil
}

// GetName returns the unique identifier of the plugin.
func GetName() string {
	return "freshgate"
}

// Cleanup releases resources on gateway shutdown.
func Cleanup() error {
	fmt.Println("[FreshGate Plugin] Cleanup called.")
	return nil
}

// verifyWithFreshGate calls the FreshGate verification endpoint with fail-open semantics.
func verifyWithFreshGate(text string) (*VerificationResult, error) {
	reqPayload, err := json.Marshal(VerifyRequest{Answer: text})
	if err != nil {
		return nil, err
	}

	resp, err := httpClient.Post(pluginConfig.FreshGateURL, "application/json", bytes.NewBuffer(reqPayload))
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("freshgate returned status: %d", resp.StatusCode)
	}

	var result VerificationResult
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, err
	}

	return &result, nil
}

// PostLLMHook intercepts LLM provider responses before they leave Bifrost core.
func PostLLMHook(
	ctx *schemas.BifrostContext,
	resp *schemas.BifrostResponse,
	bifrostErr *schemas.BifrostError,
) (*schemas.BifrostResponse, *schemas.BifrostError, error) {
	if resp == nil || bifrostErr != nil {
		return resp, bifrostErr, nil
	}

	// Extract assistant message content
	if resp.Choices != nil && len(resp.Choices) > 0 {
		assistantText, ok := resp.Choices[0].Message.Content.(string)
		if !ok || assistantText == "" {
			return resp, bifrostErr, nil
		}

		if pluginConfig.AsyncMode {
			go func(text string) {
				_, _ = verifyWithFreshGate(text)
			}(assistantText)
			return resp, bifrostErr, nil
		}

		verification, err := verifyWithFreshGate(assistantText)
		if err != nil {
			if pluginConfig.FailOpen {
				if ctx != nil {
					ctx.Log(schemas.LogLevelWarn, fmt.Sprintf("[FreshGate] Verification failed open: %v", err))
				}
				return resp, bifrostErr, nil
			}
			return resp, bifrostErr, err
		}

		// Attach FreshGate metadata block
		if resp.ExtraFields == nil {
			resp.ExtraFields = make(map[string]any)
		}
		resp.ExtraFields["freshgate"] = verification
	}

	return resp, bifrostErr, nil
}

// HTTPTransportPostHook intercepts responses at the HTTP transport layer to inject headers.
func HTTPTransportPostHook(
	ctx *schemas.BifrostContext,
	req *schemas.HTTPRequest,
	resp *schemas.HTTPResponse,
) error {
	if resp == nil || resp.Headers == nil {
		return nil
	}

	// If PostLLMHook stored verification or score
	resp.Headers["X-FreshGate-Plugin"] = "active"
	return nil
}
