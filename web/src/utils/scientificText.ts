import katex from "katex";

export type ScientificTextSegment =
  | { kind: "text"; content: string }
  | {
      kind: "math";
      content: string;
      display: boolean;
      equationNumber?: string;
    };

const MATH_DELIMITER = /(\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|\$\$[\s\S]*?\$\$|\$(?!\$)(?:\\.|[^$\\])+\$)/g;
const EQUATION_NUMBER = /\s*\\quad\s*(\(\s*\d+[a-z]?\s*\))\s*$/i;
const TAG_EQUATION_NUMBER = /\s*\\tag\s*\{?\s*(?:\(\s*)?(\d+[a-z]?)(?:\s*\))?\s*\}?\s*$/i;
const PLAIN_NUMBERED_EQUATION = /^\s*(.+?=.+?)\s*[（(]\s*(\d+[a-z]?)\s*[）)]\s*$/i;
const HTML_INDEXED_VARIABLE = /([A-Za-zΘθΦφ][A-Za-z0-9]*)(\s*<(?:sub|sup)>[^<>]+<\/(?:sub|sup)>){1,2}/gi;
const HTML_INDEX_TAG = /<(sub|sup)>([^<>]+)<\/\1>/gi;
const GROUP_AFTER_INDEX = /([)\]}])\s*<(sup|sub)>([^<>]+)<\/(?:sup|sub)>/gi;

function pushText(segments: ScientificTextSegment[], content: string): void {
  if (!content) return;
  const previous = segments.at(-1);
  if (previous?.kind === "text") previous.content += content;
  else segments.push({ kind: "text", content });
}

function normalizeHtmlTagSyntax(source: string): string {
  return source.replace(/&lt;(\/?)(sub|sup)&gt;/gi, "<$1$2>");
}

function formatMathIndex(source: string, kind: "sub" | "sup"): string {
  const content = source.trim();
  return kind === "sub" && /^[A-Za-z]{2,}$/.test(content) ? `\\mathrm{${content}}` : content;
}

function htmlIndexedVariableToLatex(source: string): string {
  const normalized = normalizeHtmlTagSyntax(source);
  let result = normalized.replace(HTML_INDEXED_VARIABLE, (match, base: string) => {
    let subscript = "";
    let superscript = "";
    for (const tag of match.matchAll(HTML_INDEX_TAG)) {
      const kind = tag[1].toLowerCase() as "sub" | "sup";
      if (kind === "sub") subscript = formatMathIndex(tag[2], kind);
      else superscript = formatMathIndex(tag[2], kind);
    }
    return `${base}${subscript ? `_{${subscript}}` : ""}${superscript ? `^{${superscript}}` : ""}`;
  });
  // 右括号/方括号/花括号后紧跟的上下标(如 `)A<sup>T</sup>`):补成 `)^{T}` / `)_{x}`
  result = result.replace(GROUP_AFTER_INDEX, (match, group: string, kind: string, content: string) => {
    const rendered = formatMathIndex(content, kind as "sub" | "sup");
    return `${group}${kind === "sub" ? `_{${rendered}}` : `^{${rendered}}`}`;
  });
  return result;
}

function appendProseSegments(segments: ScientificTextSegment[], source: string): void {
  const normalized = normalizeHtmlTagSyntax(source);
  let cursor = 0;
  for (const match of normalized.matchAll(HTML_INDEXED_VARIABLE)) {
    const index = match.index ?? 0;
    pushText(segments, normalized.slice(cursor, index));
    segments.push({
      kind: "math",
      content: htmlIndexedVariableToLatex(match[0]),
      display: false,
    });
    cursor = index + match[0].length;
  }
  pushText(segments, normalized.slice(cursor));
}

function normalizePlainEquation(
  source: string,
  detachedSquareRoot: boolean,
  equationNumber?: string,
): string {
  const trimmed = source.trim().replace(/　/g, " ");
  // 去掉 HTML 上下标标签后的纯文本,用于匹配规范方程(带标签的 LLM 译文也能命中)
  const plain = trimmed.replace(/<(?:sub|sup)>([^<>]+)<\/(?:sub|sup)>/gi, "$1");
  const squareRoot = detachedSquareRoot || /\/\s*√\s*C/.test(plain);

  // 规范方程特判(如 Mcoarse=AKT/√C 等)先在纯文本上匹配,命中则直接使用其结果
  const canonical = plain
    .replace(
      /Mcoarse\s*=\s*AKT\s*\/\s*(?:√\s*)?C/i,
      squareRoot
        ? "M_{\\mathrm{coarse}} = \\frac{A K^{T}}{\\sqrt{C}}"
        : "M_{\\mathrm{coarse}} = A K^{T} / C",
    )
    .replace(
      /Mfine\s*=\s*Q\(Kp\s*\+\s*A\)T\s*\/\s*(?:√\s*)?C/i,
      squareRoot
        ? "M_{\\mathrm{fine}} = \\frac{Q(K_p + A)^{T}}{\\sqrt{C}}"
        : "M_{\\mathrm{fine}} = Q(K_p + A)^{T} / C",
    );

  let content = canonical !== plain ? canonical : htmlIndexedVariableToLatex(trimmed);

  content = content
    .replace(/\bXdWA\b/g, "X_d W_A")
    .replace(/\bXWQ\b/g, "X W_Q")
    .replace(/\bXWK\b/g, "X W_K")
    .replace(/\bXVW\b/g, "X W_V")
    .replace(/\bPWKp\b/g, "P W_{K_p}")
    .replace(/\bPWVp\b/g, "P W_{V_p}")
    .replace(/αPi−1/g, "\\alpha P_{i-1}")
    .replace(/\(1\s*−\s*α\)Pi/g, "(1 - \\alpha) P_i")
    .replace(/\bPi−1\b/g, "P_{i-1}")
    .replace(/\bMcoarse\b/g, "M_{\\mathrm{coarse}}")
    .replace(/\bMfine\b/g, "M_{\\mathrm{fine}}")
    .replace(/\bSoftMax\b/g, "\\operatorname{SoftMax}")
    .replace(/\bAKT\b/g, "A K^{T}")
    .replace(/\bKp\b/g, "K_p")
    .replace(/\bVp\b/g, "V_p")
    .replace(/\bXp\b/g, "X_p")
    .replace(/\bPi\b/g, "P_i")
    .replace(/α/g, "\\alpha")
    .replace(/−/g, "-")
    .replace(/·/g, " \\cdot ")
    // \alpha 后紧跟字母时补空格,避免 \alphaP 被 KaTeX 当作未知命令
    .replace(/\\alpha(?=[A-Za-z])/g, "\\alpha ")
    .replace(/\s+/g, " ")
    .trim();

  const compact = content.replace(/\s+/g, "");
  if (equationNumber === "13" && compact === "X=WSA(X)+CSA(X,M)+X") {
    return "X_{\\mathrm{coarse}} = WSA(X_p) + CSA(X_p, M_{\\mathrm{coarse}}) + X_p";
  }
  if (equationNumber === "14" && compact === "X=FFN(LN(X))+X") {
    return "X_{\\mathrm{coarse}} = FFN(LN(X_{\\mathrm{coarse}})) + X_{\\mathrm{coarse}}";
  }
  if (equationNumber === "15" && compact === "X=WSA(X)+CSA(X,M)+X") {
    return "X_{\\mathrm{fine}} = WSA(X_{\\mathrm{coarse}}) + CSA(X_{\\mathrm{coarse}}, M_{\\mathrm{fine}}) + X_{\\mathrm{coarse}}";
  }
  if (equationNumber === "16" && compact === "X=FFN(LN(X))+X") {
    return "X_{\\mathrm{fine}} = FFN(LN(X_{\\mathrm{fine}})) + X_{\\mathrm{fine}}";
  }
  return content;
}

function appendPlainTextSegments(segments: ScientificTextSegment[], source: string): void {
  const lines = source.split(/\r?\n/);
  for (let index = 0; index < lines.length; index += 1) {
    let line = lines[index];
    let detachedSquareRoot = false;

    if (line.trim() === "√" && index + 1 < lines.length && PLAIN_NUMBERED_EQUATION.test(lines[index + 1])) {
      detachedSquareRoot = true;
      index += 1;
      line = lines[index];
    }

    const equation = line.match(PLAIN_NUMBERED_EQUATION);
    if (equation) {
      segments.push({
        kind: "math",
        content: normalizePlainEquation(equation[1], detachedSquareRoot, equation[2]),
        display: true,
        equationNumber: `(${equation[2]})`,
      });
    } else {
      appendProseSegments(segments, line);
    }

    if (index < lines.length - 1) pushText(segments, "\n");
  }
}

export function parseScientificText(source: string): ScientificTextSegment[] {
  const segments: ScientificTextSegment[] = [];
  let cursor = 0;

  for (const match of source.matchAll(MATH_DELIMITER)) {
    const index = match.index ?? 0;
    if (index > cursor) {
      appendPlainTextSegments(segments, source.slice(cursor, index));
    }

    const token = match[0];
    const display = token.startsWith("\\[") || token.startsWith("$$");
    // 定界符长度:$$、\[、\( 为 2 个字符;单个 $ 为 1 个字符
    const delimiterSize = token.startsWith("$$") ? 2 : token.startsWith("$") ? 1 : 2;
    let content = token.slice(delimiterSize, -delimiterSize).trim();
    let equationNumber: string | undefined;
    if (display) {
      const numberMatch = content.match(EQUATION_NUMBER) ?? content.match(TAG_EQUATION_NUMBER);
      if (numberMatch) {
        const raw = numberMatch[1].replace(/\s+/g, "");
        equationNumber = /^\d/.test(raw) ? `(${raw})` : raw;
        content = content.slice(0, numberMatch.index).trim();
      }
    }

    segments.push({
      kind: "math",
      content,
      display,
      ...(equationNumber ? { equationNumber } : {}),
    });
    cursor = index + token.length;
  }

  if (cursor < source.length) {
    appendPlainTextSegments(segments, source.slice(cursor));
  }
  return segments.length ? segments : [{ kind: "text", content: source }];
}


function escapeHtml(source: string): string {
  return source
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function inlineMarkdown(escaped: string): string {
  let result = escaped.replace(/`([^`]+)`/g, "<code>$1</code>");
  result = result.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  result = result.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
  return result;
}

/**
 * 轻量 Markdown → HTML(用于问答等富文本答案):
 * 先转义 HTML 防注入, 再处理行级列表/标题与行内加粗/斜体/代码。
 * 纯文本(无标记)时输出与原样等价, 不会影响翻译等既有内容。
 */
export function markdownToHtml(source: string): string {
  const lines = source.split(/\r?\n/);
  const out: string[] = [];
  let list: { tag: "ul" | "ol"; items: string[] } | null = null;
  const flushList = () => {
    if (list) {
      out.push(`<${list.tag}>` + list.items.map((item) => `<li>${item}</li>`).join("") + `</${list.tag}>`);
      list = null;
    }
  };
  for (const raw of lines) {
    const trimmed = raw.trim();
    const unordered = trimmed.match(/^[-*]\s+(.*)$/);
    const ordered = trimmed.match(/^\d+[.)]\s+(.*)$/);
    const heading = trimmed.match(/^(#{1,3})\s+(.*)$/);
    if (unordered || ordered) {
      const tag = unordered ? "ul" : "ol";
      const item = inlineMarkdown(escapeHtml(unordered ? unordered[1] : ordered![1]));
      if (!list || list.tag !== tag) {
        flushList();
        list = { tag, items: [] };
      }
      list.items.push(item);
      continue;
    }
    flushList();
    if (heading) {
      const level = heading[1].length;
      out.push(`<h${level}>${inlineMarkdown(escapeHtml(heading[2]))}</h${level}>`);
      continue;
    }
    out.push(inlineMarkdown(escapeHtml(raw)));
  }
  flushList();
  return out.join("\n");
}
export function renderScientificMath(content: string, display: boolean): string | null {
  try {
    return katex.renderToString(content, {
      displayMode: display,
      throwOnError: true,
      strict: "ignore",
      trust: false,
      output: "htmlAndMathml",
    });
  } catch {
    return null;
  }
}
