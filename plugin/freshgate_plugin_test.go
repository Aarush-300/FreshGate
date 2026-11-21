package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/maximhq/bifrost/core/schemas"
)

func TestChatVerificationReachesTransport(t *testing.T) {
	report := `{"claims":[{"sources":[{"snippet":"release notes"}]}],"summary":{"avg_score":null,"status":"completed_with_warnings","stale":0}}`
	verifier := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var request VerifyRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil || request.Answer != "Check this release." || r.Header.Get("X-API-Key") != "fixture" {
			t.Error("incorrect verification request")
		}
		io.WriteString(w, report)
	}))
	defer verifier.Close()
	t.Setenv("FRESHGATE_URL", verifier.URL)
	t.Setenv("FRESHGATE_API_KEY", "fixture")
	t.Setenv("FRESHGATE_ASYNC_MODE", "false")
	if err := Init(nil); err != nil {
		t.Fatal(err)
	}

	transport := schemas.NewBifrostContext(context.Background(), schemas.NoDeadline)
	HTTPTransportPreHook(transport, nil)
	core := schemas.NewBifrostContext(context.Background(), schemas.NoDeadline)
	for key, value := range transport.GetUserValues() {
		core.SetValue(key, value)
	}
	chat := &schemas.BifrostChatResponse{}
	if err := json.Unmarshal([]byte(`{"choices":[{"message":{"role":"assistant","content":"Check this release."}}]}`), chat); err != nil {
		t.Fatal(err)
	}
	chat.ExtraFields.RawResponse = "provider trace"
	_, failure, err := PostLLMHook(core, &schemas.BifrostResponse{ChatResponse: chat}, nil)
	if err != nil || failure != nil || chat.ExtraFields.RawResponse != "provider trace" {
		t.Fatal("completion or provider trace lost")
	}
	response := &schemas.HTTPResponse{StatusCode: 200, Body: []byte(`{"id":"abc","usage":{"total_tokens":9}}`)}
	if err := HTTPTransportPostHook(transport, nil, response); err != nil {
		t.Fatal(err)
	}
	var body map[string]json.RawMessage
	if err := json.Unmarshal(response.Body, &body); err != nil {
		t.Fatal(err)
	}
	if string(body["freshgate"]) != report || string(body["id"]) != `"abc"` || string(body["usage"]) != `{"total_tokens":9}` || response.Headers["X-FreshGate-Score"] != "unavailable" {
		t.Fatalf("unexpected response %s, headers %v", response.Body, response.Headers)
	}
}
