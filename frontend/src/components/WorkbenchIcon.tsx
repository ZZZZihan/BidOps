import type { SVGProps } from 'react';

export type IconName = 'workflow' | 'agent' | 'archive' | 'company' | 'screening' | 'jobs' | 'reviews' | 'ingestion' | 'audit' | 'arrow' | 'plus' | 'shield' | 'file' | 'chevron' | 'logout';

const paths: Record<IconName, string> = {
  workflow: 'M4 5h6v6H4z M14 13h6v6h-6z M10 8h6v5 M7 11v5h7',
  agent: 'm12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z M19 3v4 M17 5h4',
  archive: 'M4 4h16v4H4z M6 8v12h12V8 M10 12h4',
  company: 'M4 21V7l8-4v18 M12 9h8v12 M8 8v1 M8 12v1 M8 16v1 M16 13v1 M16 17v1 M2 21h20',
  screening: 'M3 5h18 M6 12h12 M10 19h4 M7 3v4 M16 10v4 M12 17v4',
  jobs: 'M9 4h6v4H9z M9 6H5v15h14V6h-4 M8 12h8 M8 16h5',
  reviews: 'M9 3H5v18h14V3h-4 M9 3v4h6V3z m-1 10-4 4-2-2',
  ingestion: 'M12 3v12 m-4-4 4 4 4-4 M4 14v6h16v-6',
  audit: 'M5 3h10l4 4v14H5z M14 3v5h5 M8 12h8 M8 16h5',
  arrow: 'M4 12h16 m-6-6 6 6-6 6',
  plus: 'M12 5v14 M5 12h14',
  shield: 'm12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6z m-4 9 3 3 5-5',
  file: 'M6 3h8l4 4v14H6z M13 3v5h5 M9 12h6 M9 16h4',
  chevron: 'm9 5 7 7-7 7',
  logout: 'M10 4H4v16h6 M9 12h12 m-4-4 4 4-4 4',
};

export function WorkbenchIcon({name, ...props}: SVGProps<SVGSVGElement> & {name: IconName}) {
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}><path d={paths[name]} /></svg>;
}

export function BidOpsMark() {
  return <svg className="bidops-mark" width="35" height="35" viewBox="0 0 36 36" fill="none" aria-hidden="true"><path d="m6 10 12-6 12 6-12 6Z" fill="currentColor"/><path d="m6 17 12 6 12-6M6 24l12 6 12-6" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"/></svg>;
}

/** Decorative evidence graph; does not represent query results or live data. */
export function EvidenceGraph() {
  return <svg className="evidence-graph" viewBox="0 0 320 230" fill="none" aria-hidden="true"><g stroke="currentColor" strokeWidth=".7" opacity=".2"><path d="M25 156 160 78l135 78-135 78ZM25 121l135-78 135 78-135 78ZM25 86 160 8l135 78-135 78Z"/><path d="M25 86v70M160 8v70M295 86v70M160 164v70 M70 60v35l135 78v35 M115 34v35l135 78v35 M70 112v70M115 138v70 M205 34v35L70 147v35 M250 60v35l-135 78v35 M205 138v70M250 112v70"/></g><path d="m160 61 52 30v60l-52 30-52-30V91Z" fill="var(--accent-soft)" stroke="currentColor" strokeWidth="1.2"/><path d="m108 91 52 30 52-30 M160 121v60" stroke="currentColor" strokeWidth="1.2"/><path d="m137 104 23-13 23 13-23 13Z" fill="currentColor" opacity=".8"/><g fill="var(--surface)" stroke="currentColor" strokeWidth="1.4"><circle cx="25" cy="121" r="4"/><circle cx="295" cy="86" r="4"/><circle cx="160" cy="8" r="4"/><circle cx="115" cy="208" r="4"/><circle cx="250" cy="182" r="4"/></g><path d="M38 32h33M38 38h19M249 212h34M264 218h19" stroke="currentColor" opacity=".4"/></svg>;
}
