package main

import (
	"errors"
	"strings"
	"testing"

	"github.com/egoist/mygo"
)

func TestDownloadCancelDoesNotSaveOrReportCompletion(t *testing.T) {
	endpoint, _ := parseEndpoint(defaultServiceURL)
	var notices []downloadNotice
	controller := &downloadController{
		endpoint: endpoint,
		save:     func(string) (string, error) { return "", nil },
		notify:   func(n downloadNotice) { notices = append(notices, n) },
	}
	event := &mygo.DownloadEvent{URL: "blob:" + defaultServiceURL + "fixture-export", Path: "/tmp/proposed.json"}
	controller.start(event)
	if !event.DefaultPrevented() {
		t.Fatal("canceling the save dialog must cancel the download")
	}
	// A platform callback after cancellation must not claim failure or success.
	controller.finish(&mygo.Download{URL: event.URL, Path: event.Path, Err: errors.New("canceled")})
	controller.finish(&mygo.Download{URL: event.URL, Path: event.Path})
	if len(notices) != 0 || len(controller.pending) != 0 {
		t.Fatalf("canceled download produced notices=%d or pending=%d", len(notices), len(controller.pending))
	}
}

func TestDownloadWaitsForConfirmedNativeResult(t *testing.T) {
	endpoint, _ := parseEndpoint(defaultServiceURL)
	for _, failed := range []bool{false, true} {
		t.Run(map[bool]string{false: "saved", true: "failed"}[failed], func(t *testing.T) {
			var notices []downloadNotice
			controller := &downloadController{
				endpoint: endpoint,
				save:     func(string) (string, error) { return "/tmp/chosen-fixture.json", nil },
				notify:   func(n downloadNotice) { notices = append(notices, n) },
			}
			event := &mygo.DownloadEvent{URL: defaultServiceURL + "api/reviews/fixture/export", Path: "/tmp/proposed.json"}
			controller.start(event)
			if event.DefaultPrevented() || event.Path != "/tmp/chosen-fixture.json" || len(notices) != 0 {
				t.Fatal("selecting a destination must not report that a download has already finished")
			}
			result := &mygo.Download{URL: event.URL, Path: event.Path}
			wantKind := mygo.MessageInfo
			if failed {
				result.Err = errors.New("sensitive-source-or-server-detail")
				wantKind = mygo.MessageError
			}
			controller.finish(result)
			controller.finish(result) // Ignore duplicate completion callbacks.
			if len(notices) != 1 || notices[0].kind != wantKind || !strings.Contains(notices[0].detail, event.Path) {
				t.Fatalf("unexpected download result: %+v", notices)
			}
			if strings.Contains(notices[0].detail, "sensitive-source") || strings.Contains(notices[0].detail, event.URL) {
				t.Fatal("download notices must not expose source URLs or raw server errors")
			}
		})
	}
}

func TestDownloadRejectsOtherOriginsBeforeSaveDialog(t *testing.T) {
	endpoint, _ := parseEndpoint(defaultServiceURL)
	for _, source := range []string{
		"https://example.org/attachment.json", "http://127.0.0.1:8766/file.json",
		"blob:http://127.0.0.1:8766/fixture", "file:///tmp/fixture.json",
	} {
		t.Run(source, func(t *testing.T) {
			var notices []downloadNotice
			controller := &downloadController{
				endpoint: endpoint,
				save: func(string) (string, error) {
					t.Fatal("untrusted download opened a save dialog")
					return "", nil
				},
				notify: func(n downloadNotice) { notices = append(notices, n) },
			}
			event := &mygo.DownloadEvent{URL: source}
			controller.start(event)
			if !event.DefaultPrevented() || len(notices) != 1 || notices[0].kind != mygo.MessageError {
				t.Fatal("another origin's download was not visibly rejected")
			}
		})
	}
}

func TestSaveDialogFailureCancelsDownloadWithOneError(t *testing.T) {
	endpoint, _ := parseEndpoint(defaultServiceURL)
	var notices []downloadNotice
	controller := &downloadController{
		endpoint: endpoint,
		save:     func(string) (string, error) { return "", errors.New("dialog unavailable") },
		notify:   func(n downloadNotice) { notices = append(notices, n) },
	}
	event := &mygo.DownloadEvent{URL: "blob:" + defaultServiceURL + "fixture-export", Path: "/tmp/proposed.json"}
	controller.start(event)
	controller.finish(&mygo.Download{URL: event.URL, Path: event.Path, Err: errors.New("canceled")})
	if !event.DefaultPrevented() || len(notices) != 1 || notices[0].kind != mygo.MessageError || len(controller.pending) != 0 {
		t.Fatal("save dialog failure did not cancel cleanly with one actionable error")
	}
}
