package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"mime"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

const defaultServiceURL = "http://127.0.0.1:8765/"

type serviceEndpoint struct{ url *url.URL }

func parseEndpoint(raw string) (serviceEndpoint, error) {
	u, err := url.Parse(raw)
	if err != nil || strings.ContainsAny(raw, "?#") || u.Scheme != "http" || u.Hostname() != "127.0.0.1" ||
		u.User != nil || (u.Path != "" && u.Path != "/") || u.RawPath != "" ||
		u.RawQuery != "" || u.ForceQuery || u.Fragment != "" || u.Opaque != "" {
		return serviceEndpoint{}, errors.New("服务地址须为 http://127.0.0.1:端口/，不能含登录信息或其他路径")
	}
	port, err := strconv.Atoi(u.Port())
	if err != nil || port < 1 || port > 65535 {
		return serviceEndpoint{}, errors.New("服务地址需要 1–65535 范围内的明确端口")
	}
	u.Host = "127.0.0.1:" + strconv.Itoa(port)
	u.Path = "/"
	return serviceEndpoint{url: u}, nil
}

func (s serviceEndpoint) String() string { return s.url.String() }

func (s serviceEndpoint) sameOrigin(raw string) bool {
	u, err := url.Parse(raw)
	return err == nil && u.User == nil && u.Scheme == s.url.Scheme && u.Host == s.url.Host
}

func (s serviceEndpoint) ownsBlob(raw string) bool {
	u, err := url.Parse(raw)
	return err == nil && u.Scheme == "blob" && s.sameOrigin(u.Opaque)
}

func (s serviceEndpoint) recoverableLoadFailure(failedURL, currentURL string, loading bool) bool {
	if s.sameOrigin(failedURL) {
		return true
	}
	// Some native webviews omit the failing URL for provisional navigation.
	// Recover only when the known current page or pending load is our service,
	// so a failure of the built-in page cannot create a reload loop.
	return failedURL == "" && (s.sameOrigin(currentURL) || loading)
}

func isOfflineURL(raw string) bool {
	u, err := url.Parse(raw)
	if err != nil || u.User != nil {
		return false
	}
	return (u.Scheme == "mygo" && u.Host == "localhost") ||
		(u.Scheme == "http" && u.Host == "mygo.localhost")
}

func isExternalWebURL(raw string) bool {
	u, err := url.Parse(raw)
	if err != nil || u.User != nil || u.Hostname() == "" || (u.Scheme != "https" && u.Scheme != "http") {
		return false
	}
	host := strings.ToLower(u.Hostname())
	if host == "localhost" || strings.HasSuffix(host, ".localhost") {
		return false
	}
	if ip := net.ParseIP(host); ip != nil && (ip.IsLoopback() || ip.IsUnspecified() || ip.IsPrivate() || ip.IsLinkLocalUnicast()) {
		return false
	}
	return true
}

func probeClient() *http.Client {
	return &http.Client{
		Timeout: 3 * time.Second,
		Transport: &http.Transport{
			// The configured service is loopback-only. Never route it through an
			// environment proxy, or reuse a browser's credentials or cookie jar.
			Proxy:                  nil,
			DialContext:            (&net.Dialer{Timeout: 2 * time.Second}).DialContext,
			DisableKeepAlives:      true,
			MaxResponseHeaderBytes: 64 << 10,
		},
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
}

// probeService only reads the public HTML entry point. It never calls a
// business API, sends cookies, follows redirects, or starts a backend process.
func probeService(ctx context.Context, client *http.Client, endpoint serviceEndpoint) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint.String(), nil)
	if err != nil {
		return errors.New("无法准备本机连接")
	}
	req.Header.Set("Accept", "text/html, application/xhtml+xml")
	response, err := client.Do(req)
	if err != nil {
		return errors.New("还无法连接本机服务，请确认服务已启动后重试")
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("本机服务返回 HTTP %d，请确认启动状态后重试", response.StatusCode)
	}
	mediaType, _, err := mime.ParseMediaType(response.Header.Get("Content-Type"))
	if err != nil || (mediaType != "text/html" && mediaType != "application/xhtml+xml") {
		return errors.New("这个端口没有提供工作台页面，请确认 BidOps 服务地址")
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, 1<<20))
	if err != nil {
		return errors.New("工作台页面未读取完整，请重试")
	}
	// Current BidOps pages identify themselves in the document title. Requiring
	// that marker avoids opening an unrelated HTML service on a reused port.
	document := strings.ToLower(string(body))
	start := strings.Index(document, "<title>")
	end := strings.Index(document, "</title>")
	if start < 0 || end <= start || !strings.Contains(document[start:end], "bidops") {
		return errors.New("这个端口的页面未标识为 BidOps，请确认服务地址与前端构建")
	}
	return nil
}
