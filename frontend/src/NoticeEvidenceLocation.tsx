import { createContext, useContext, useEffect, useRef, useState } from 'react';
import { ApiError, request } from './api';
import './NoticeEvidenceLocation.css';

// This context only supplies the current request credentials and the page's
// existing access-denied cleanup. It does not cache evidence or location data.
export const NoticeLocationContext = createContext<{
  csrfToken: string | null;
  onAccessDenied: (failure: ApiError) => void;
} | null>(null);

type Reference = {side?: string; object_kind?: string};
type Location = {kind: 'pdf_pages'; pages: number[]; parser_receipt_sha256: string}
  | {kind: 'normalized_text_v2'; start: number; end: number};

function Lookup({body, csrfToken, onAccessDenied}: {
  body: string; csrfToken: string | null; onAccessDenied: (failure: ApiError) => void;
}) {
  const [location, setLocation] = useState<Location | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => {controller.current?.abort();}, []);

  async function check() {
    controller.current?.abort();
    const current = new AbortController(); controller.current = current;
    setBusy(true); setError(''); setLocation(null);
    try {
      const response = await request<{location: Location}>('/notice-evidence/location', {
        method: 'POST', signal: current.signal,
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken || ''},
        // Keep the original B01 EvidenceRef intact. Its page field is never
        // used to produce the location shown below.
        body,
      });
      if (current.signal.aborted) return;
      const next = response.location;
      if (next?.kind === 'pdf_pages' && next.pages.length > 0
        && next.pages.every(page => Number.isInteger(page) && page > 0)
        && /^[a-f0-9]{64}$/.test(next.parser_receipt_sha256)) setLocation(next);
      else if (next?.kind === 'normalized_text_v2' && Number.isInteger(next.start)
        && Number.isInteger(next.end) && next.start >= 0 && next.end > next.start) setLocation(next);
      else setError('服务端未返回可核对的页定位，请重新读取引用后再试。');
    } catch (failure) {
      if (current.signal.aborted) return;
      if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) {
        onAccessDenied(failure); return;
      }
      setError(failure instanceof ApiError && failure.status === 422
        ? '此固定引用或解析记录当前无法核对，未确认 PDF 页号。'
        : '页定位核对未完成，请重试；未确认 PDF 页号。');
    } finally {if (!current.signal.aborted) setBusy(false);}
  }

  return <div className="notice-evidence-location">
    <button type="button" disabled={busy || !csrfToken} onClick={() => void check()}>核对PDF页定位</button>
    {busy && <p role="status">正在核对服务端保存的附件页定位…</p>}
    {error && <p role="alert">{error}</p>}
    {location?.kind === 'pdf_pages' && <div className="notice-evidence-location-result" role="status">
      <p>服务端核对页码：第 {location.pages.join('、')} 页</p>
      <dl><dt>解析记录 SHA-256（receipt）</dt><dd><code>{location.parser_receipt_sha256}</code></dd></dl>
      <p>页码来自固定附件的已保存解析记录，不使用引用携带的页码。</p>
    </div>}
    {location?.kind === 'normalized_text_v2' && <p className="notice-evidence-location-result" role="status">此附件没有已保存的 PDF 页定位；服务端仅核对规范化文本字符 [{location.start}, {location.end})，不能据此确认页号。</p>}
  </div>;
}

export function NoticeEvidenceLocation({evidence}: {evidence: Reference}) {
  const context = useContext(NoticeLocationContext);
  if (!context || evidence.side !== 'notice' || evidence.object_kind !== 'attachment') return null;
  const body = JSON.stringify(evidence);
  // A different fixed reference or identity must unmount and abort the old
  // lookup immediately, including when React reuses a condition card.
  return <Lookup key={`${context.csrfToken}:${body}`} body={body} {...context} />;
}
