// Package main implements FreshGate as a native Bifrost Go dynamic plugin.
//
// This plugin intercepts completed LLM responses, extracts and verifies volatile factual
// claims against Tavily and an LLM judge via the FreshGate API,
// and decorates the response with freshness headers and a "freshgate" metadata payload.
//
// Compile with:
//   go build -buildmode=plugin -o freshgate.so freshgate_plugin.go

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync/atomic"
	"time"

	"github.com/maximhq/bifrost/core/schemas"
)

type Config struct {
	FreshGateURL string `json:"freshgate_url"`
	APIKey       string `json:"api_key"`
	TimeoutSec   int    `json:"timeout_sec"`
	FailOpen     bool   `json:"fail_open"`
	AsyncMode    bool   `json:"async_mode"`
}

var activeConfig atomic.Pointer[Config]

type VerifyRequest struct {
	Answer string `json:"answer"`
}

// The transport and LLM hooks run with separate contexts. Bifrost forwards
// values from the transport pre-hook into the core request context.
const resultKey = schemas.BifrostContextKey("freshgate-verification")

type requestResult struct {
	body   json.RawMessage
	status string
	score  *int
	stale  int
}

func HTTPTransportPreHook(ctx *schemas.BifrostContext, req *schemas.HTTPRequest) (*schemas.HTTPResponse, error) {
	ctx.SetValue(resultKey, &atomic.Pointer[requestResult]{})
	return nil, nil
}

// Init initializes the FreshGate plugin with configuration from config.json.
func Init(config any) error {
	pluginConfig := Config{FreshGateURL: "http://127.0.0.1:8000/api/plugin/verify", TimeoutSec: 4, FailOpen: true}
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
		if apiKey, ok := values["api_key"].(string); ok && apiKey != "" {
			pluginConfig.APIKey = apiKey
		}
	}
	if url := os.Getenv("FRESHGATE_URL"); url != "" {
		pluginConfig.FreshGateURL = url
	}
	if key := os.Getenv("FRESHGATE_API_KEY"); key != "" {
		pluginConfig.APIKey = key
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
	activeConfig.Store(&pluginConfig)
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
func verifyWithFreshGate(text string, pluginConfig *Config) (*requestResult, error) {
	reqPayload, err := json.Marshal(VerifyRequest{Answer: text})
	if err != nil {
		return nil, err
	}

	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(pluginConfig.TimeoutSec)*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, "POST", pluginConfig.FreshGateURL, bytes.NewBuffer(reqPayload))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	if pluginConfig.APIKey != "" {
		req.Header.Set("X-API-Key", pluginConfig.APIKey)
	}

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("freshgate returned status: %d", resp.StatusCode)
	}

	var body json.RawMessage
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		return nil, err
	}
	var report struct {
		Summary *struct {
			Status string `json:"status"`
			Score  *int   `json:"avg_score"`
			Stale  int    `json:"stale"`
		} `json:"summary"`
	}
	if err := json.Unmarshal(body, &report); err != nil || report.Summary == nil || report.Summary.Status == "" {
		return nil, fmt.Errorf("invalid FreshGate verification summary")
	}
	return &requestResult{body: body, status: report.Summary.Status, score: report.Summary.Score, stale: report.Summary.Stale}, nil
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

	// Bifrost's current schemas wrap chat completions in ChatResponse and use
	// ChatMessageContent rather than the legacy OpenAI-shaped response fields.
	if resp.ChatResponse != nil && len(resp.ChatResponse.Choices) > 0 {
		choice := resp.ChatResponse.Choices[0].ChatNonStreamResponseChoice
		if choice == nil || choice.Message == nil || choice.Message.Content == nil || choice.Message.Content.ContentStr == nil {
			return resp, bifrostErr, nil
		}
		assistantText := strings.TrimSpace(*choice.Message.Content.ContentStr)
		if assistantText == "" {
			return resp, bifrostErr, nil
		}

		pluginConfig := activeConfig.Load()
		var slot *atomic.Pointer[requestResult]
		if ctx != nil {
			slot, _ = ctx.Value(resultKey).(*atomic.Pointer[requestResult])
		}
		if pluginConfig.AsyncMode {
			if slot != nil {
				slot.Store(&requestResult{status: "async"})
			}
			go func(text string) {
				_, _ = verifyWithFreshGate(text, pluginConfig)
			}(assistantText)
			return resp, bifrostErr, nil
		}

		verification, err := verifyWithFreshGate(assistantText, pluginConfig)
		if err != nil {
			if pluginConfig.FailOpen {
				if slot != nil {
					slot.Store(&requestResult{status: "fail-open"})
				}
				if ctx != nil {
					ctx.Log(schemas.LogLevelWarn, fmt.Sprintf("[FreshGate] Verification failed open: %v", err))
				}
				return resp, bifrostErr, nil
			}
			code := http.StatusServiceUnavailable
			return nil, &schemas.BifrostError{IsBifrostError: true, StatusCode: &code, Error: &schemas.ErrorField{Message: "FreshGate verification unavailable"}}, nil
		}
		if slot != nil {
			slot.Store(verification)
		}
	}

	return resp, bifrostErr, nil
}

// HTTPTransportPostHook intercepts responses at the HTTP transport layer to inject headers.
func HTTPTransportPostHook(
	ctx *schemas.BifrostContext,
	req *schemas.HTTPRequest,
	resp *schemas.HTTPResponse,
) error {
	if resp == nil {
		return nil
	}
	if resp.Headers == nil {
		resp.Headers = make(map[string]string)
	}
	resp.Headers["X-FreshGate-Plugin"] = "active"
	if ctx == nil || resp.StatusCode != http.StatusOK {
		return nil
	}
	slot, _ := ctx.Value(resultKey).(*atomic.Pointer[requestResult])
	if slot == nil {
		return nil
	}
	result := slot.Load()
	if result == nil {
		return nil
	}
	resp.Headers["X-FreshGate-Status"] = result.status
	if result.body == nil {
		return nil
	}
	resp.Headers["X-FreshGate-Stale-Count"] = strconv.Itoa(result.stale)
	score := "unavailable"
	if result.score != nil {
		score = strconv.Itoa(*result.score)
	}
	resp.Headers["X-FreshGate-Score"] = score
	var completion map[string]json.RawMessage
	if err := json.Unmarshal(resp.Body, &completion); err != nil || completion == nil {
		return nil
	}
	completion["freshgate"] = result.body
	body, err := json.Marshal(completion)
	if err != nil {
		return err
	}
	resp.Body = body
	return nil
}
