/** 文献检索 Agent 类型定义 */

export type SearchSource = "arxiv" | "semantic_scholar" | "openalex";
export type OaStatus = "open" | "closed" | "unknown";

export interface SearchResult {
  source: SearchSource;
  title: string;
  authors: string[];
  year: number | null;
  venue: string;
  abstract: string;
  doi: string | null;
  pdf_url: string | null;
  page_url: string;
  citations: number;
  published: boolean;
  ccf_level: "A" | "B" | "C" | null;
  oa_status: OaStatus;
  openalex_id: string | null;
}

export type ImportStatus = "pending" | "downloading" | "parsing" | "done" | "failed";

export interface ImportTask {
  id: number;
  title: string;
  source: string;
  status: ImportStatus;
  progress: number;
  error_message: string;
  paper_id: number | null;
  created_at: string;
  updated_at: string;
}

export type BrowserStatusType = "none" | "alive" | "expired";

export interface BrowserStatus {
  status: BrowserStatusType;
  message: string;
}

export interface ResearchPlanEvent {
  event: "plan";
  queries: string[];
  sources: string[];
  direct?: boolean;
}

export interface ResearchResultsEvent {
  event: "results";
  items: SearchResult[];
  total: number;
  offset: number;
  total_is_estimate?: boolean;
}

export interface ResearchErrorEvent {
  event: "error";
  message: string;
}

export type ResearchProgressEvent =
  | ResearchPlanEvent
  | ResearchResultsEvent
  | ResearchErrorEvent
  | { event: "done" };
