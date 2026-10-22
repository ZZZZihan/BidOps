import { useEffect, useState } from 'react';
import { get } from './api';
import type { Notice, Page, Source } from './api';

const kinds: Record<string, string> = {tender_announcement: '采购公告', correction: '更正公告', termination: '终止 / 废标', award: '中标 / 成交'};
const missing = (value: string | null | undefined) => value || '未知 · 原文未确认';
const when = (value: string | null) => value ? new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'short', hour12: false,
}).format(new Date(value)) : missing(null);
const money = (value: Notice['budget']) => value ? `${value.amount} 元` : missing(null);

export function Archive() {
  const [page, setPage] = useState<Page | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<Notice | null>(null);
  const [error, setError] = useState('');
  const [detailError, setDetailError] = useState('');
  const [connected, setConnected] = useState(false);
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError(''); setPage(null); setConnected(false);
    Promise.all([
      get<Page>(`/notices?limit=20&offset=${offset}`, controller.signal),
      get<{items: Source[]}>('/sources', controller.signal),
      get<{status: string}>('/health', controller.signal),
    ]).then(([list, sourceList, health]) => {
      setPage(list); setSources(sourceList.items); setConnected(health.status === 'ok');
    }).catch((e: Error) => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [offset, revision]);
  useEffect(() => {
    const controller = new AbortController();
    setDetail(null); setDetailError('');
    if (selectedId) get<Notice>(`/notices/${encodeURIComponent(selectedId)}`, controller.signal)
      .then(setDetail).catch((e: Error) => {if (!controller.signal.aborted) setDetailError(e.message);});
    return () => controller.abort();
  }, [selectedId, revision]);
  return <>
    <main>
      <span className="connection">{connected ? '本地服务已连接' : '等待本地服务'}</span>
      <div className="intro"><div><h1>采购公告</h1><p>查看已导入的原文、字段与证据。所有时间以北京时间显示。</p></div><button onClick={() => setRevision(v => v + 1)}>刷新列表</button></div>
      <div className="source-strip"><strong>官方来源</strong>{sources.map(s => <a key={s.id} href={s.home_url} target="_blank" rel="noreferrer">{s.name}<span>{s.robots_status === 'allowed' && s.terms_status === 'allowed' ? '已记录访问许可' : '访问许可待核查'}</span></a>)}</div>
      {error ? <div className="message error" role="alert">无法读取公告：{error}。请检查本地后端服务后刷新。</div> : !page ? <p role="status">正在读取公告…</p> : <>
        <div className="list-heading"><h2>已导入公告 <span>{page.total}</span></h2><p>更正与终止公告保留独立记录</p></div>
        <div className="workspace">
          <section aria-label="公告列表">
            {page.items.length === 0 ? <div className="empty"><h3>尚未导入公告</h3><p>先通过命令行导入依法持有的公告文件，再刷新列表。</p><p>种子 URL 只是来源线索，未取得正文的公告不会显示为成功导入。</p></div> : <div className="table-scroll"><table><thead><tr><th>项目 / 采购单位</th><th>类型</th><th>预算</th><th>投标截止</th></tr></thead><tbody>{page.items.map(n => <tr key={n.notice_id} className={n.notice_id === selectedId ? 'selected' : ''}><td><button className="notice-title" onClick={() => setSelectedId(n.notice_id)}>{missing(n.title)}</button><small>{missing(n.project_code)}</small><small>{missing(n.buyer_name)}</small></td><td>{n.notice_kind ? kinds[n.notice_kind] || n.notice_kind : '未知'}<small className="uncertain">{n.status_as_observed === 'review_required' ? '需要人工复核' : '当前状态未知'}</small></td><td>{money(n.budget)}</td><td>{when(n.bid_deadline_at)}</td></tr>)}</tbody></table></div>}
            {page.total > 20 && <nav aria-label="分页"><button disabled={offset === 0} onClick={() => setOffset(v => Math.max(0, v - 20))}>上一页</button><span>{offset + 1}–{Math.min(offset + 20, page.total)} / {page.total}</span><button disabled={offset + 20 >= page.total} onClick={() => setOffset(v => v + 20)}>下一页</button></nav>}
          </section>
          <aside aria-label="公告详情">
            <h2>原文与证据</h2>{detailError ? <p role="alert">详情读取失败：{detailError}</p> : !detail ? <p>{selectedId ? '正在读取详情…' : '从列表选择一条公告，查看来源及缺失字段。'}</p> : <>
              <h3>{missing(detail.title)}</h3><a href={detail.source.source_url} target="_blank" rel="noreferrer">打开官方来源</a>
              <dl>{[['项目编号', detail.project_code], ['采购单位', detail.buyer_name], ['预算', money(detail.budget)], ['公告日期', detail.published_at ? when(detail.published_at) : detail.published_date], ['投标截止', when(detail.bid_deadline_at)], ['文件获取截止', when(detail.document_deadline_at)], ['采集时间', when(detail.source.fetched_at)], ['获取方式', detail.source.access_method === 'manual_import' ? '人工文件导入' : '许可范围内 HTTP 获取']].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{missing(value)}</dd></div>)}</dl>
              <p className="notice">仅含公告正文；附件、资格条件和当前状态需要人工复核。</p>
              {detail.packages.length > 0 && <section><h3>采购包</h3>{detail.packages.map((p, i) => <p key={i}>{p.label}：{money(p.budget)}</p>)}</section>}
              <h3>中小企业相关原文</h3>{detail.eligibility_conditions.length ? detail.eligibility_conditions.map((e, i) => <blockquote key={i}>{e.text}<small>原文字符 {e.start}–{e.end}</small></blockquote>) : <p className="uncertain">未知 · 未定位相关原文</p>}
              <details><summary>字段证据与缺失原因</summary>{Object.entries(detail.abstain_reason).map(([key, reason]) => <p key={key}><strong>{key}</strong>：{reason}</p>)}{Object.entries(detail.field_evidence).map(([key, entries]) => <div key={key}><strong>{key}</strong>{entries.map((e, i) => <blockquote key={i}>{e.text}<small>{e.locator} · 字符 [{e.start}, {e.end})</small></blockquote>)}</div>)}</details>
              <details><summary>完整性校验</summary><p className="hash">SHA256: {detail.source.sha256}</p><ul>{detail.parse_warnings.map(w => <li key={w}>{w}</li>)}</ul></details>
            </>}
          </aside>
        </div>
      </>}
    </main>
  </>;
}
