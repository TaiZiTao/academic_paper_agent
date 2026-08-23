import type {
  PaperCitation,
  PaperDetail,
  PaperStatus,
  PaperTaskRequest,
} from "@/types/paper";
import {
  deletePaper,
  getPaperDetail,
  listPapers,
  retryPaper,
  uploadPaper,
} from "@/api/paper";

const status: PaperStatus = "ready";
const citation: PaperCitation = {
  paper_id: 1,
  paper_title: "Paper",
  page: 2,
  section: "Methods",
  chunk_id: "paper-1-chunk-0",
  quote: "evidence",
  verified: true,
  reason: "",
};
const request: PaperTaskRequest = {
  task_type: "qa",
  input_text: "What is the method?",
  session_id: "session-1",
};

type DetailPromise = ReturnType<typeof getPaperDetail>;
const _detailPromise: Promise<PaperDetail> | null = null;
void status;
void citation;
void request;
void (_detailPromise as DetailPromise | null);
void listPapers;
void uploadPaper;
void retryPaper;
void deletePaper;
