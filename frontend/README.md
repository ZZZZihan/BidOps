# BidOps 采购证据工作台

现有业务前端使用 React 19、TypeScript 与 Vite，提供完整流程、Agent 助手、公告档案、企业画像与材料、候选与资格核对、持久核对任务、人工复核、授权更新与导入及运行审计。实际入口由服务端身份、权限和本机流程能力决定。

界面采用浅蓝与淡白配色；组件来自 21st.dev 发布的 shadcn/ui、Prompt Kit 和 Glow Card Grid，并按现有 React/CSS 结构适配。出处、固定版本、许可与改动说明见 [UI-COMPONENT-SOURCES.md](../docs/UI-COMPONENT-SOURCES.md)。没有新增运行依赖或改变 API 合同。

## 开发与构建

在本目录执行（Node >= 22.12）：

```sh
npm ci
npm run dev
npm run build
```

开发与预览默认将 `/api` 转发到 `http://127.0.0.1:8000`。连接其他已启动服务时可单次指定 `BIDOPS_API_PROXY_TARGET`，例如：

```sh
BIDOPS_API_PROXY_TARGET=http://127.0.0.1:8765 npm run dev
```

所有业务请求继续使用相对 `/api`、原 Cookie、CSRF 与幂等协议。生产构建写入 `dist/`；构建最后会将组件和 React 的 MIT 全文汇总到 `dist/THIRD-PARTY-LICENSES.txt`。发布静态文件时须保留该文件。

本机完整流程需按 [LOCAL-WORKFLOW-DEMO.md](../docs/LOCAL-WORKFLOW-DEMO.md) 启动已有服务。前端构建本身不导入资料、不准备示例企业、不启用模型，也不迁移数据。MyGo 桌面入口位于 [desktop/](../desktop/README.md)，直接加载已启动的同源 BidOps 服务，使用同一份前端构建。具体依赖与验收见 [TECH-WORKBENCH-PLAN.md](../docs/TECH-WORKBENCH-PLAN.md)。

## 回归与视觉检查

```sh
BIDOPS_BROWSER_CHANNEL=chrome npm run test:e2e -- --workers=4
```

175 条现有用例包含拦截 API 的浏览器协议测试，以及实际临时 FastAPI/SQLite/ACL/DBOS 的集成测试。模型和外部身份方使用离线替身，不能将它们视为真实模型、真实采购材料或企业 SSO 验收。临时后端用例需要根目录独立 `.venv` 与 `requirements/durable.lock`、`requirements/attachments.lock` 中的锁定依赖。

测试不会复用已经占用的预览端口，以免误测其他工作树。默认 4173 被占用时，选择空闲端口：

```sh
BIDOPS_BROWSER_PORT=4187 BIDOPS_NETWORK_ENABLED=false \
  BIDOPS_BROWSER_CHANNEL=chrome npm run test:e2e -- --workers=4
```

`frontend/output/playwright/` 用于本机视觉检查，不提交临时脚本、trace 或完整页面缓存。代表性界面截图和本轮验证结论见 [验收记录](../docs/reports/tech-workbench-ui-20261026.md)。截图使用明确的协议测试数据。

## 展示边界

- 页面上的公告数量、固定版本与人工决定数来自已有响应；尚未读取的值显示为待准备／尚未筛选／尚未复核。
- 满足、不满足、信息不足、证据冲突、不适用保留明确文字；颜色只作为辅助，不把任务结束等同于资格通过。
- 手机保留全部授权导航入口，表单和证据区按窄屏布局；支持键盘操作和系统减少动态效果偏好。
- `src/workbench.css` 是共享主题和布局，业务模块 CSS 沿用原布局与状态语义。`src/components/ui/` 是本地开源组件适配；`api.ts` 与后端接口保持原样。
