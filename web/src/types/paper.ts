export type PaperStatus =
  | "uploaded"
  | "parsing"
  | "indexing"
  | "reporting"
  | "ready"
  | "failed";

export type PaperTaskType = "qa" | "translation" | "presentation" | "review";

export interface PaperSummary {
  id: number;
  original_filename: string;
  title: string;
  authors: string[];
  abstract: string;
  keywords: string[];
  language: "zh" | "en" | "mixed" | "unknown";
  page_count: number;
  status: PaperStatus;
  /** 研究方向(LLM 自动分类, 可手动修改) */
  research_field: string;
  error_code: string;
  error_message: string;
  created_at: string;
  updated_at: string;
}

export interface PaperSection {
  id: number;
  title: string;
  normalized_title: string;
  level: number;
  ordinal: number;
  page_start: number;
  page_end: number;
  summary: string;
}

export interface PaperCitation {
  paper_id: number;
  paper_title: string;
  page: number | null;
  section: string;
  chunk_id: string;
  quote: string;
  verified: boolean;
  reason: string;
}

export interface PaperReviewContent {
  summary: string;
  contributions: string[];
  strengths: string[];
  major_issues: string[];
  minor_issues: string[];
  ratings: {
    novelty: string;
    correctness: string;
    experiments: string;
    writing: string;
  };
  suggestions: string[];
  recommendation: string;
  score: number | null;
}
export interface PaperArtifact {
  id: number;
  task_id: number | null;
  artifact_type: "report" | PaperTaskType;
  title: string;
  content: Record<string, unknown>;
  content_text: string;
  citations: PaperCitation[];
  created_at: string;
}

export interface PaperTranslationBlock {
  id?: number;
  section?: string;
  block_index: number;
  page_start: number;
  page_end: number;
  content: string;
  status: "pending" | "running" | "completed" | "failed";
  error_message?: string;
}

export interface PaperMessage {
  id: number;
  session_id: string;
  role: "user" | "assistant";
  content: string;
  citations: PaperCitation[];
  suggestions?: string[];
  created_at: string;
}

export interface PaperFigure {
  id: number;
  page: number;
  kind: "figure" | "table";
  ordinal: number;
  caption: string;
  caption_translated: string;
  image_url: string;
}

export interface PaperDetail {
  paper: PaperSummary;
  sections: PaperSection[];
  artifacts: PaperArtifact[];
  messages: PaperMessage[];
  translation_blocks: PaperTranslationBlock[];
  figures: PaperFigure[];
}

export interface PaperGroup {
  field: string;
  count: number;
  items: PaperSummary[];
}

export interface PaperListResponse {
  items: PaperSummary[];
  total: number;
  /** 分组视图(group=true)时返回 */
  groups?: PaperGroup[];
  /** 全量研究方向清单 */
  fields?: string[];
}

export interface PaperTaskRequest {
  task_type: PaperTaskType;
  input_text: string;
  session_id?: string;
  section?: string | null;
}

export interface PaperProgressEvent {
  event: "progress" | "done" | "error";
  stage: string;
  status: PaperStatus;
  message?: string;
}

export interface PaperTaskDoneEvent {
  task_id: number;
  artifact_id: number;
  content: string;
  citations: PaperCitation[];
  /** 翻译时被过滤掉的段落(表格数据/图注清理), 非空时提示用户可能不完整 */
  warnings?: string[];
}

export interface PaperTaskCallbacks {
  onProgress?: (data: Record<string, unknown>) => void;
  onToken?: (content: string) => void;
  onBlock?: (data: PaperTranslationBlock) => void;
  onFigure?: (data: PaperFigure) => void;
  onDone?: (data: PaperTaskDoneEvent) => void;
  onError?: (detail: string) => void;
}
