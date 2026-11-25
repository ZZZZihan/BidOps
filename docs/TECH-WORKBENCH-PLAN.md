# BidOps 科技工作台：实施范围与验收

日期：2026-10-26。用户要求在 **BidOps 现有项目** 上使用 MyGo 与更多 21st.dev 组件，保留业务功能和接口；最终配色指定为原有的 **浅蓝、淡白**。不创建替代产品或独立业务前端。

基线为 `feature/demo-workflow` 的 `fcd5e1b9ad9ba752bf9c45f607723f5eaaa5a25c`（PR #29，尚未合并），依赖 PR #28。原 `demo-workflow` 工作树保持不变。本轮在隔离工作树 `mygo-tech-ui` 实施。

## PR 顺序

| PR 范围 | 工作分支 / base | 目标 | 依赖 |
| --- | --- | --- | --- |
| UI-01：业务界面 | `feature/tech-workbench-ui` / `feature/demo-workflow` | 统一九个视图、21st.dev 本地组件、浅蓝淡白主题、桌面与手机布局、许可分发和测试端口隔离 | PR #29 现有业务 |
| DESKTOP-01：MyGo 入口 | `feature/mygo-desktop` / `feature/tech-workbench-ui` | 用 MyGo v0.6.4 加载原同源服务，离线连接页、明确重试、失败恢复及审核包下载 | UI-01；用户原有本机服务 |

主 Agent 统一进行本轮分支提交、推送及 PR 创建。三个子 Agent 分别承担组件及许可适配、MyGo 实现和原生验证、独立业务/技术复核。子 Agent 请求配置均为 GPT-6.1 Sol / ultra；请求参数不等于独立核验实际推理运行档位。

## 验收项

| AC-ID | 类别 | 可检查结果 |
| --- | --- | --- |
| UI-01 | C | 浅蓝淡白主题覆盖九个现有视图；使用真实开源组件适配，并保留来源、固定提交及 MIT 全文 |
| UI-02 | C | 原身份与导航权限、全部相对 `/api`、Cookie、CSRF、幂等恢复、CAS 和授权导出保持兼容 |
| UI-03 | C | 五种判断、未知/过期/缺件/冲突、原始模型判断和独立人工决定继续清楚展示 |
| UI-04 | C | 现有 175 条浏览器回归完成；区分协议替身与实际临时后端集成；不调用真实模型 |
| UI-05 | C | 桌面和 390px 手机可用，无横向页面溢出，输入法与键盘提交保留；减少动效偏好生效 |
| DESKTOP-01 | C | 固定 MyGo v0.6.4，加载用户已启动的同源本机服务，不增加接口代理或复制后端 |
| DESKTOP-02 | C | 服务未启动、错误页面、超时、加载失败和显式重试有可见结果；不自动重放业务请求 |
| DESKTOP-03 | C | 原生 WKWebView 验证同源 Cookie、登录回调、外部网页边界和 Blob 下载实际落盘 |
| ACCEPT-01 | B | 用户对整体视觉和真实业务流程进行人工验收；真实模型、真实材料、企业 IdP/HTTPS 与生产发布独立验收 |

## 不扩大范围的约束

不改数据库、迁移、真实资料、费用账本、模型配置、API 合同、评分器或提示词；不调用 `scripts/check_demo_browser.sh` 的付费路径。测试只使用临时目录、离线模型替身与协议夹具。

基线 PR #29 的 backend CI 已有失败：`test_collected_usage_cannot_override_original_saved_result` 的测试 manifest 提示版本仍为 `qualification-v1`，而当前实现是 `qualification-v7-evidence-completeness`。两次运行均为 1 failed / 2279 passed / 1 skipped。该问题先于本轮，不能把前端通过表述成整个仓库 CI 全绿；本轮不降低后端版本校验来绕过它。

本轮不合并、不部署、不公证或对外发布桌面应用。回退可关闭对应 PR 或在后续合并后 revert 对应提交，重新构建既有前端；桌面入口独立，无业务数据回退需求。
