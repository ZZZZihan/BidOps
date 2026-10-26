# UI 组件来源与本地适配

本次界面使用了在 **21st.dev 真实发布的开源组件**，并根据该组件的公开上游源码进行本地适配。组件存放于 `frontend/src/components/ui/`，使用 React 和普通 CSS，**未新增运行依赖**。所有下载的源码与许可于 2026-10-26 核查；本文件使用固定提交地址，避免上游后续修改影响溯源。

21st.dev 当前安装说明要求 registry API key，页面将完整组件源码访问关联到账户。本次没有登录、读取密钥、调用生成服务或绕过访问限制；组件源码来自各作者公开的 GitHub 仓库。21st.dev 的发布页用于确认组件、作者和库的对应关系，公开上游的固定提交与许可文件用于核对实际适配来源。

## 已采用的组件

| 本地实现 | 21st.dev 发布页 / 作者 | 实际适配的公开源码 | 许可 |
| --- | --- | --- | --- |
| `Sidebar`、`SidebarHeader`、`SidebarContent`、`SidebarGroup`、`SidebarGroupLabel`、`SidebarGroupContent`、`SidebarMenu`、`SidebarMenuItem`、`SidebarMenuButton`、`SidebarFooter` | [Sidebar](https://21st.dev/@shadcn/components/sidebar) / shadcn | [sidebar.tsx，固定提交](https://github.com/shadcn-ui/ui/blob/481d523374745e77d0cf9c53a4115ab5dd7c8481/apps/v4/registry/new-york-v4/ui/sidebar.tsx) | MIT，Copyright © 2023 shadcn |
| `Button` | [Button](https://21st.dev/@shadcn/components/button) / shadcn | [button.tsx，固定提交](https://github.com/shadcn-ui/ui/blob/481d523374745e77d0cf9c53a4115ab5dd7c8481/apps/v4/registry/new-york-v4/ui/button.tsx) | MIT，Copyright © 2023 shadcn |
| `Badge` | [shadcn/ui 组件目录](https://21st.dev/@shadcn/library/shadcn-ui) / shadcn，目录包含 Badge | [badge.tsx，固定提交](https://github.com/shadcn-ui/ui/blob/481d523374745e77d0cf9c53a4115ab5dd7c8481/apps/v4/registry/new-york-v4/ui/badge.tsx) | MIT，Copyright © 2023 shadcn |
| `Card`、`CardHeader`、`CardTitle`、`CardDescription`、`CardAction`、`CardContent`、`CardFooter` | [Card](https://21st.dev/@shadcn/components/card) / shadcn | [card.tsx，固定提交](https://github.com/shadcn-ui/ui/blob/481d523374745e77d0cf9c53a4115ab5dd7c8481/apps/v4/registry/new-york-v4/ui/card.tsx) | MIT，Copyright © 2023 shadcn |
| `Empty`、`EmptyHeader`、`EmptyMedia`、`EmptyTitle`、`EmptyDescription`、`EmptyContent` | [Empty](https://21st.dev/@shadcn/components/empty) / shadcn | [empty.tsx，固定提交](https://github.com/shadcn-ui/ui/blob/481d523374745e77d0cf9c53a4115ab5dd7c8481/apps/v4/registry/new-york-v4/ui/empty.tsx) | MIT，Copyright © 2023 shadcn；21st 的聚合许可元数据为 unknown，此处依据实际公开上游许可 |
| `PromptInput`、`PromptInputTextarea`、`PromptInputActions` | [Prompt Input](https://21st.dev/@ibelick/components/prompt-input) / Julien Thibeaut，Prompt Kit | [prompt-input.tsx，固定提交](https://github.com/ibelick/prompt-kit/blob/5a9496635cc0e1bf7679fe2a9e35d9b36b99c08a/components/prompt-kit/prompt-input.tsx)、[公开 registry](https://github.com/ibelick/prompt-kit/blob/5a9496635cc0e1bf7679fe2a9e35d9b36b99c08a/public/c/prompt-input.json) | MIT，Copyright © 2025 Julien Thibeaut |
| `GlowCard` | [Glow Card Grid](https://21st.dev/@ncdai/components/glow-card-grid) / Chánh Đại | [glow-card-grid.tsx，固定提交](https://github.com/ncdai/chanhdai.com/blob/4e66bb3e3abd4fa793f5a5b4ab24c77bb17c315f/src/registry/components/glow-card-grid/glow-card-grid.tsx)、[公开 registry](https://github.com/ncdai/chanhdai.com/blob/4e66bb3e3abd4fa793f5a5b4ab24c77bb17c315f/public/r/glow-card-grid.json) | MIT，Copyright © 2026 Chánh Đại |

这批组件采用同一套本地设计变量：`--surface`、`--surface-raised`、`--border`、`--text`、`--muted`、`--accent`、`--accent-soft`。CSS 选择器全部限定在 `ui-` 类名内，不对所有原生按钮、标题、表单或其他页面作全局覆盖。语义状态色可通过 `--ui-success`、`--ui-warning`、`--ui-danger`、`--ui-info` 调整；强调按钮前景色可通过 `--ui-on-accent` 调整。

## 适配内容与行为边界

- **shadcn 基础结构**：保留组件分工、组合方式、`data-slot`、按钮 variant/size、卡片的 header/content/footer 组织及空状态的 media/title/description/content 组织。将 Tailwind 类、`cn` 和 `class-variance-authority` 转成局部 CSS 与显式属性。未使用 `asChild`，因此没有引入 Radix Slot。`CardTitle` 与 `EmptyTitle` 使用原生 `h3`，描述使用原生 `p`。
- **Sidebar**：采用原版的侧栏组合结构与活动项模式，提供原生 `aside`、`ul`、`li`、`button`，活动按钮具有 `aria-current="page"`。这是适合现有应用的结构子集，不包含原版的 cookie 持久化、快捷键、折叠 Provider 或移动端 Sheet；布局与窄屏导航由应用主题负责。组件本身不设置固定宽高。
- **Button**：支持 `default` / `secondary` / `outline` / `ghost` / `destructive` / `link`，以及 `default` / `sm` / `lg` / `icon` 四种尺寸。保留原生事件、`disabled`、表单属性和引用。没有传入 `type` 时保留浏览器原生语义，因此表单中的非提交操作必须明确设置 `type="button"`。
- **Badge**：保留原版独立标签、variant 与内容组合方式，加入业务需要的 `success`、`warning`、`danger`、`info`、`neutral` 状态，以及可选静态圆点。没有脉冲动画；标签必须包含明确文字，颜色只作辅助。
- **Prompt Input**：基于原版 Context、受控/内部值、自动增高 textarea 和独立操作区的结构。原生 textarea 替代上游 Textarea；没有引入 Tooltip 和图标库。支持 `value`、`onValueChange`、`disabled`、`isLoading`、`maxHeight`；原生 textarea 的 `id`、`aria-*`、`maxLength`、`onChange`、`onKeyDown` 等继续透传。组件不接管 Enter 提交，调用方保留现有中文输入法合成保护、Shift+Enter、请求状态与字数限制。自动增高使用 `useLayoutEffect`，并在宽度变化时重算；观察器在清理时断开。
- **Glow Card**：基于原版的指针位置、CSS 自定义属性、双层遮罩边框与光晕思路，将头像专用展示改为通用 `children`。普通 CSS 替代 Tailwind；移除文档级监听，将指针响应限定在卡片内，并通过 `requestAnimationFrame` 合并更新。触屏、无法悬停的指针和减少动效偏好下不启用响应光晕；媒体查询监听、待处理帧均有清理。只用于少量流程卡，避免大面积高亮。

`index.ts` 统一导出组件并引入 `ui.css`。组件的 API 与行为是本地适配版本，不代表未经修改安装了 21st.dev registry 的完整产物。Tabs 本轮未采用，因为目前没有需要新增的真实标签页面板切换。

## 完整许可文本

MIT 的版权和许可全文已随本地组件保留，不依赖外链可用性：

- `frontend/src/components/ui/LICENSES/shadcn-ui.txt`；上游 [LICENSE.md](https://github.com/shadcn-ui/ui/blob/481d523374745e77d0cf9c53a4115ab5dd7c8481/LICENSE.md)。
- `frontend/src/components/ui/LICENSES/prompt-kit.txt`；上游 [LICENCE.md](https://github.com/ibelick/prompt-kit/blob/5a9496635cc0e1bf7679fe2a9e35d9b36b99c08a/LICENCE.md)。
- `frontend/src/components/ui/LICENSES/chanhdai.txt`；上游 [LICENSE](https://github.com/ncdai/chanhdai.com/blob/4e66bb3e3abd4fa793f5a5b4ab24c77bb17c315f/LICENSE)。

未采用许可不明确、明确无许可或与页面标注不一致的候选，也未复制第三方插画、头像或其他独立授权素材。UI 调整不会自动改变公告、预算、资格、证据、权限或演示数据的真实性；这些业务行为仍由原有应用与测试验证。
