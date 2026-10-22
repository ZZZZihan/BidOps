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
export async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api${path}`, {signal});
  if (!response.ok) throw new Error(`接口返回 ${response.status}`);
  return response.json() as Promise<T>;
}
