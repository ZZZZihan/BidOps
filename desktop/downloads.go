package main

import (
	"sync"

	"github.com/egoist/mygo"
)

type downloadNotice struct {
	kind            mygo.MessageType
	message, detail string
}

type downloadKey struct{ url, path string }

// downloadController only reports completion for downloads for which the user
// chose a destination. Canceled dialogs never produce a success or error notice.
type downloadController struct {
	endpoint serviceEndpoint
	save     func(defaultPath string) (string, error)
	notify   func(downloadNotice)
	mu       sync.Mutex
	pending  map[downloadKey]int
}

func (c *downloadController) start(event *mygo.DownloadEvent) {
	if !c.endpoint.sameOrigin(event.URL) && !c.endpoint.ownsBlob(event.URL) {
		event.PreventDefault()
		c.notify(downloadNotice{
			kind: mygo.MessageError, message: "这个文件未保存",
			detail: "桌面工作台仅保存当前本机服务提供的文件。其他来源请在系统浏览器中打开。",
		})
		return
	}
	path, err := c.save(event.Path)
	if err != nil || path == "" {
		event.PreventDefault()
		if err != nil {
			c.notify(downloadNotice{
				kind: mygo.MessageError, message: "暂时无法选择保存位置",
				detail: "文件尚未保存。请再次导出并选择可写入的位置。",
			})
		}
		return
	}
	event.Path = path
	c.mu.Lock()
	if c.pending == nil {
		c.pending = make(map[downloadKey]int)
	}
	c.pending[downloadKey{event.URL, path}]++
	c.mu.Unlock()
}

func (c *downloadController) finish(download *mygo.Download) {
	key := downloadKey{download.URL, download.Path}
	c.mu.Lock()
	count := c.pending[key]
	if count <= 1 {
		delete(c.pending, key)
	} else {
		c.pending[key]--
	}
	c.mu.Unlock()
	if count == 0 {
		return
	}
	if download.Err != nil {
		c.notify(downloadNotice{
			kind: mygo.MessageError, message: "文件未能保存完成",
			detail: "请确认本机服务仍在运行，并重新导出。此位置可能留有未完成文件：\n" + download.Path,
		})
		return
	}
	c.notify(downloadNotice{
		kind: mygo.MessageInfo, message: "文件已保存",
		detail: "保存位置：\n" + download.Path,
	})
}
