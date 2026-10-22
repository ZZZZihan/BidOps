# Data source feasibility / source registry

## Authority and legal access (read this first)

A procurement announcement is *public to read*, which **does not automatically permit automated large-scale crawling, unrestricted reuse, or distribution of whole bidding attachments**. Inspect site Terms, robots.txt and contractual/portal access before automated reads. Do not bypass HTTP 403, CAPTCHA, anti-bot controls, or required CA certificates. Do not harvest authenticated documents. Prefer official download/export feeds where published and approved. Set user agent, conservative rate limits, no concurrent burst. Manual curation is always acceptable as fallback.

### Preferred pilot: official government procurement notices

1. [中国政府采购网 / information announcements](https://www.ccgp.gov.cn/xxgg/) — official Ministry of Finance designated procurement information portal. Search/index availability and programmatic access **not guaranteed**.
2. [中国政府采购网 / data interface specifications](https://www.ccgp.gov.cn/sjbzjgf/) — has interface specification v1.2 listed (2026-03-16). **Do not assume that publication of a spec grants anonymous/public access**; verify credentials and terms.
3. [天津市公共资源交易平台](https://ggzy.zwfwb.tj.gov.cn/) and [天津市政府采购网](https://ccgp-tianjin.gov.cn/) — optional secondary sources, legal/technical feasibility to be checked independently.

### Real primary-notice seed URLs (initially URL-only; six public bodies sampled on 2026-10-22)

The user-requested follow-up returned HTTP 200 for all six URLs below. Raw bodies are
private and excluded from Git. See [actual capture results](../reports/p0-collection-20261022.md).
This finite access check does not establish permission for recurring crawling or redistribution.

- TGPC-2026-A-0384, 天津市生态环境监测中心 环境监测仪器, 2026-10-16: https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202610/t20261016_27510682.htm
- TGPC-2026-D-1131, 天津市环境应急与事故调查中心 云资源及安全服务, 2026-10-19: https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202610/t20261019_27520096.htm
- TJBH-2026-D-0170, 滨海新区政府办公室 网络与办公设备运维, 2026-10-19: https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202610/t20261019_27520100.htm
- TGPC-2026-A-0343, 天津中医药大学第二附属医院 数字化扫描硬件, 2026-10-16: https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202610/t20261016_27510616.htm
- PYGP-2026-A-0826, 天津计量监督检测科研院 检测仪器, 2026-10-18: https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202610/t20261018_27513837.htm
- TGPC-2026-A-0324, 天津疾控中心 信息化建设, 2026-09-18 (historical): https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202609/t20260918_27358274.htm

**IMPORTANT**: Some notices explicitly state that bidding-document downloads require a digital CA certificate/login, or on-site purchase. These attachments are **NOT** automatically collectable. A public summary is still useful without the full invitation pack; show an explicit `source_completeness=notice_only` and `eligibility=review_required`.

## Initial collection goal (P0)

- Start with 6 seed URLs; test access and capture provenance, errors, content type, encoding.
- If robots/site rules forbid automatic retrieval, use a human-supplied lawful HTML/text fixture and **do not claim the pipeline downloaded it**.
- **No bulk crawler** in P0. Once permission confirmed, operator supplies a finite whitelist.
- Dataset label: public primary URL; not 'training dataset licensed for redistribution'.

## Data threats

- Type aliases, procurement-method drift, unit ambiguity (`万元` vs `元`), multiple packages, corrected deadlines, supplier SME requirements vs manufacturer SME requirements, HTML attachment availability, expiration time, date and geography.
- Notice publication date != upload deadline != document download deadline != competition deadline.
- If multiple sources mirror the same project, store both links but dedupe via official project ID + publisher when justified; do not merge away amendments.
