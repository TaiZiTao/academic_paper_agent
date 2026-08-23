const CAPTION = /^\s*(?:(?:fig(?:ure)?\.?|table)\s*(?:\d+[a-z]?|[ivxlcdm]+)\s*[:：.]|(?:图|表)\s*(?:\d+[a-z]?|[ivxlcdm]+)\s*[:：.])/i;
const SECTION_HEADING = /^\s*(?:(?:[IVXLC]+|\d+(?:\.\d+)*|[A-Z])[.、]\s+)[A-Za-z\u4e00-\u9fff]/;
const NUMBERED_EQUATION = /^\s*.+?=.+?[（(]\s*\d+[a-z]?\s*[）)]\s*$/i;
const BODY_START = /^\s*(?:The|We|This|These|After|Following|前文|后文|我们|随后|因此|其中|为此|该方法|这些)/i;
const VISUAL_WORDS = new Set([
  "anchor", "attention", "categorize", "conv", "downscale", "pixel", "prompting", "shuffle", "uncategorized",
]);

function looksLikeProseOrHeading(line: string): boolean {
  const text = line.trim();
  if (!text) return false;
  if (SECTION_HEADING.test(text) || NUMBERED_EQUATION.test(text)) return true;
  const chineseCount = (text.match(/[\u4e00-\u9fff]/g) || []).length;
  const latinWords = (text.match(/[A-Za-z]{2,}/g) || []).length;
  if (chineseCount >= 5 && /[，。；！？]\s*$/.test(text)) return true;
  if (latinWords >= 7 && /[.!?]\s*$/.test(text)) return true;
  return text.length >= 60 && (chineseCount >= 8 || latinWords >= 8);
}

function looksLikeStrongBoundary(line: string): boolean {
  const text = line.trim();
  if (!text) return false;
  if (/^(?:以及)?\s*[（(][a-z][）)]/i.test(text)) return false;
  if (SECTION_HEADING.test(text) || NUMBERED_EQUATION.test(text) || BODY_START.test(text)) return true;
  const chineseCount = (text.match(/[\u4e00-\u9fff]/g) || []).length;
  const latinWords = (text.match(/[A-Za-z]{2,}/g) || []).length;
  return text.length >= 60 && (chineseCount >= 8 || latinWords >= 8);
}

function hasReversedVisualWord(line: string): boolean {
  return (line.match(/[A-Za-z]{4,}/g) || [])
    .some((token) => VISUAL_WORDS.has([...token.toLowerCase()].reverse().join("")));
}

function reversedLabelGroups(lines: string[]): Array<[number, number]> {
  const indexes = lines
    .map((line, index) => (hasReversedVisualWord(line) ? index : -1))
    .filter((index) => index >= 0);
  if (indexes.length < 2) return [];
  const groups: number[][] = [[indexes[0]]];
  for (const index of indexes.slice(1)) {
    const group = groups[groups.length - 1];
    if (index - group[group.length - 1] <= 20) group.push(index);
    else groups.push([index]);
  }
  return groups
    .filter((group) => group.length >= 2)
    .map((group) => [group[0], group[group.length - 1]]);
}

export function stripVisualRegions(content: string): string {
  const lines = content.split(/\r?\n/);
  const captionIndexes = lines
    .map((line, index) => (CAPTION.test(line) ? index : -1))
    .filter((index) => index >= 0);
  const reversedGroups = reversedLabelGroups(lines);
  if (!captionIndexes.length && !reversedGroups.length) return content;

  const removed = new Set<number>();
  for (const captionIndex of captionIndexes) {
    removed.add(captionIndex);

    let cursor = captionIndex - 1;
    let scanned = 0;
    while (cursor >= 0 && scanned < 40) {
      if (looksLikeProseOrHeading(lines[cursor])) break;
      removed.add(cursor);
      cursor -= 1;
      scanned += 1;
    }

    let captionCursor = captionIndex;
    while (captionCursor < lines.length - 1 && !/[.!?。！？]\s*$/.test(lines[captionCursor].trim())) {
      captionCursor += 1;
      removed.add(captionCursor);
    }

    cursor = captionCursor + 1;
    scanned = 0;
    while (cursor < lines.length && scanned < 60) {
      if (looksLikeProseOrHeading(lines[cursor])) break;
      removed.add(cursor);
      cursor += 1;
      scanned += 1;
    }
  }

  for (const [firstMarker, lastMarker] of reversedGroups) {
    let cursor = firstMarker - 1;
    let scanned = 0;
    while (cursor >= 0 && scanned < 40) {
      if (looksLikeStrongBoundary(lines[cursor])) break;
      removed.add(cursor);
      cursor -= 1;
      scanned += 1;
    }
    for (let index = firstMarker; index <= lastMarker; index += 1) removed.add(index);
    cursor = lastMarker + 1;
    scanned = 0;
    while (cursor < lines.length && scanned < 60) {
      if (looksLikeStrongBoundary(lines[cursor])) break;
      removed.add(cursor);
      cursor += 1;
      scanned += 1;
    }
  }

  return lines
    .filter((_, index) => !removed.has(index))
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}
