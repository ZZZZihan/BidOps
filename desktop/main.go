package main

import (
	"context"
	"embed"
	"flag"
	"io/fs"
	"log"
	"net/http"
	"os"
	"sync"

	"github.com/egoist/mygo"
)

//go:embed offline
var offlineFiles embed.FS

// ConnectionStatus is limited to connection information, with no business data.
type ConnectionStatus struct {
	State   string `json:"state"`
	Address string `json:"address"`
	Message string `json:"message"`
}

// Desktop connects the built-in start page to the existing loopback service.
// No extra origins receive permission to call these methods.
type Desktop struct {
	mu         sync.Mutex
	endpoint   serviceEndpoint
	client     *http.Client
	ctx        context.Context
	cancel     context.CancelFunc
	window     *mygo.Window
	status     ConnectionStatus
	attempt    uint64
	connecting bool
	closed     bool
}

// Status reports the current local connection state.
func (d *Desktop) Status() ConnectionStatus {
	d.mu.Lock()
	defer d.mu.Unlock()
	return d.status
}

// Connect makes one read-only attempt, then loads the existing same-origin app.
func (d *Desktop) Connect() ConnectionStatus {
	d.mu.Lock()
	if d.closed || d.connecting {
		status := d.status
		d.mu.Unlock()
		return status
	}
	d.connecting = true
	d.attempt++
	attempt := d.attempt
	d.status.State, d.status.Message = "connecting", "正在连接本机工作台…"
	d.mu.Unlock()

	err := probeService(d.ctx, d.client, d.endpoint)
	d.mu.Lock()
	if d.closed || d.attempt != attempt {
		status := d.status
		d.mu.Unlock()
		return status
	}
	d.connecting = false
	if err != nil {
		d.status.State, d.status.Message = "offline", err.Error()
		status := d.status
		d.mu.Unlock()
		return status
	}
	d.status.State, d.status.Message = "loading", "连接成功，正在打开工作台…"
	window := d.window
	status := d.status
	d.mu.Unlock()
	if err := window.Page().LoadURL(d.endpoint.String()); err != nil {
		d.showOffline("工作台页面未能打开，请重新连接")
		return d.Status()
	}
	return status
}

func (d *Desktop) showOffline(message string) {
	d.mu.Lock()
	if d.closed {
		d.mu.Unlock()
		return
	}
	d.attempt++
	d.connecting = false
	d.status.State, d.status.Message = "offline", message
	window := d.window
	d.mu.Unlock()
	// A failed WKWebView provisional navigation must finish its native
	// callback before another navigation starts, or the new DOM may not paint.
	go func() { _ = window.Page().LoadURL("mygo://localhost/") }()
}

func main() {
	address := os.Getenv("BIDOPS_DESKTOP_URL")
	if address == "" {
		address = defaultServiceURL
	}
	flag.StringVar(&address, "url", address, "已启动的 BidOps 本机服务地址，例如 http://127.0.0.1:8765/")
	flag.Parse()
	if flag.NArg() != 0 {
		log.Fatal("不接受额外参数；使用 -url 指定本机服务地址")
	}
	endpoint, err := parseEndpoint(address)
	if err != nil {
		log.Fatal(err)
	}
	files, err := fs.Sub(offlineFiles, "offline")
	if err != nil {
		log.Fatal(err)
	}
	mygo.SetFrontend(files)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	desktop := &Desktop{
		endpoint: endpoint,
		client:   probeClient(),
		ctx:      ctx,
		cancel:   cancel,
		status: ConnectionStatus{
			State: "idle", Address: endpoint.String(), Message: "准备连接你的本机工作台",
		},
	}
	mygo.Bind(desktop)
	mygo.App.SetName("BidOps")
	mygo.App.WhenReady(func() { desktop.openWindow() })
	if err := mygo.App.Run(); err != nil {
		log.Fatal(err)
	}
}

func (d *Desktop) openWindow() {
	window := mygo.NewWindow(mygo.WindowOptions{
		Title: "BidOps · 采购证据工作台", Width: 1440, Height: 1000,
		MinWidth: 380, MinHeight: 620, BackgroundColor: "#F0F7FB", StateKey: "workbench",
	})
	d.mu.Lock()
	d.window = window
	d.mu.Unlock()
	page := window.Page()
	downloads := &downloadController{
		endpoint: d.endpoint,
		save: func(defaultPath string) (string, error) {
			return mygo.Dialog.Save(mygo.SaveDialogOptions{
				Parent: window, Title: "保存工作台文件", DefaultPath: defaultPath,
				ButtonLabel: "保存", NameFieldLabel: "文件名：", CreateDirectories: true,
				Message: "选择审核包或附件的保存位置。取消后不会保存文件。",
			})
		},
		notify: func(notice downloadNotice) {
			// Leave the native download callback before presenting its result.
			go func() {
				d.mu.Lock()
				closed := d.closed
				d.mu.Unlock()
				if !closed {
					_, _ = mygo.Dialog.Message(mygo.MessageOptions{
						Parent: window, Type: notice.kind, Title: "BidOps · 文件保存",
						Message: notice.message, Detail: notice.detail, Buttons: []string{"知道了"},
					})
				}
			}()
		},
	}
	page.OnWillDownload(downloads.start)
	page.OnDownloadDone(downloads.finish)
	page.OnWillNavigate(func(event *mygo.NavigateEvent) {
		if d.endpoint.sameOrigin(event.URL) || d.endpoint.ownsBlob(event.URL) || isOfflineURL(event.URL) {
			return
		}
		event.PreventDefault()
		if event.UserInitiated && isExternalWebURL(event.URL) {
			go mygo.Shell.OpenExternal(event.URL)
		}
	})
	page.SetWindowOpenHandler(func(request mygo.WindowOpenRequest) *mygo.WindowOptions {
		if d.endpoint.sameOrigin(request.URL) {
			_ = page.LoadURL(request.URL)
		} else if isExternalWebURL(request.URL) {
			go mygo.Shell.OpenExternal(request.URL)
		}
		return nil
	})
	page.OnDidFailLoad(func(loadError *mygo.LoadError) {
		if d.endpoint.recoverableLoadFailure(loadError.URL, page.URL(), d.Status().State == "loading") {
			log.Printf("BidOps: 本机页面加载失败（错误码 %d）", loadError.Code)
			d.showOffline("本机工作台暂时无法打开，请确认服务仍在运行后重新连接")
		}
	})
	page.OnDidFinishLoad(func() {
		if !d.endpoint.sameOrigin(page.URL()) {
			return
		}
		d.mu.Lock()
		d.status.State, d.status.Message = "online", "已连接本机工作台"
		d.mu.Unlock()
	})
	window.OnClosed(func() {
		d.mu.Lock()
		d.closed = true
		d.mu.Unlock()
		d.cancel()
	})
	mygo.App.SetMenu(mygo.NewMenu([]*mygo.MenuItem{
		{Role: mygo.RoleAppMenu},
		{Role: mygo.RoleEditMenu},
		{Label: "工作台", Submenu: []*mygo.MenuItem{
			{Label: "连接状态与重试", Accelerator: "CmdOrCtrl+Shift+R", Click: func(*mygo.MenuItem, *mygo.Window) {
				d.showOffline("可以重新连接已启动的 BidOps 服务；原工作记录保留在服务中")
			}},
			{Label: "重新载入当前页面", Role: mygo.RoleReload},
		}},
		{Role: mygo.RoleWindowMenu},
	}))
	_ = page.LoadURL("mygo://localhost/")
}
