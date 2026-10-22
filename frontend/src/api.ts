export type Evidence = { source_snapshot_id: string; start: number; end: number; text: string; locator: string };
export type Money = { amount: string; raw: string; unit: string; basis: string; currency: string };
export type Notice = {
  notice_id: string;
  project_code: string | null;
  title: string | null;
  buyer_name: string | null;
  notice_kind: string | null;
  budget: Money | null;
  published_at: string | null;
  published_date: string | null;
  bid_deadline_at: string | null;
  document_deadline_at: string | null;
  status_as_observed: string;
  packages: {label: string; budget: Money | null; evidence: Evidence}[];
  eligibility_conditions: Evidence[];
  field_evidence: Record<string, Evidence[]>;
  abstain_reason: Record<string, string>;
  parse_warnings: string[];
  source: { source_url: string; fetched_at: string; sha256: string; access_method: string; source_completeness: string };
};
export type Page = {items: Notice[]; total: number; limit: number; offset: number};
export type Source = {id: string; name: string; home_url: string; robots_status: string; terms_status: string};
export class ApiError extends Error {
  constructor(public status: number, public code: string) { super(code); }
}
export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, options);
  if (!response.ok) {
    const data = await response.json().catch(() => ({})) as {detail?: unknown; error?: string};
    const code = typeof data.detail === 'string' ? data.detail : data.error || `http_${response.status}`;
    throw new ApiError(response.status, code);
  }
  return response.json() as Promise<T>;
}
export async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  return request<T>(path, {signal});
}
