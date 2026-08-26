const REGIONS = [
  { start: '"', end: '"', escapes: true, comment: false },
  { start: "'", end: "'", escapes: true, comment: false },
  { start: "`", end: "`", escapes: true, comment: false },
  { start: "//", end: "\n", escapes: false, comment: true },
  { start: "/*", end: "*/", escapes: false, comment: true },
];

const TAIL_LENGTH = 16;
const DIVISION_FOLLOWS = /[\w$)\]]$/;
const KEYWORD_FOLLOWS =
  /\b(?:await|case|delete|do|else|in|instanceof|new|of|return|throw|typeof|void|yield)$/;

export function lineOf(text, index) {
  return text.slice(0, index).split("\n").length;
}

function opener(text, index) {
  return REGIONS.find((region) => text.startsWith(region.start, index)) ?? null;
}

function regionEnd(text, index, opened) {
  let cursor = index + opened.start.length;
  while (cursor < text.length) {
    if (opened.escapes && text[cursor] === "\\") cursor += 2;
    else if (text.startsWith(opened.end, cursor)) return cursor + opened.end.length;
    else cursor++;
  }
  return text.length;
}

function startsARegex(tail) {
  return tail === "" || !DIVISION_FOLLOWS.test(tail) || KEYWORD_FOLLOWS.test(tail);
}

function regexEnd(text, index) {
  let cursor = index + 1;
  let inClass = false;
  while (cursor < text.length) {
    const char = text[cursor];
    if (char === "\\") cursor += 2;
    else if (char === "\n") return index + 1;
    else if (char === "/" && !inClass) return cursor + 1;
    else {
      if (char === "[") inClass = true;
      else if (char === "]") inClass = false;
      cursor++;
    }
  }
  return index + 1;
}

function nextTail(tail, char) {
  return char.trim() === "" ? tail : `${tail}${char}`.slice(-TAIL_LENGTH);
}

function codeStep(text, index, tail) {
  if (text[index] === "/" && startsARegex(tail)) {
    return { index: regexEnd(text, index), tail: "/" };
  }
  const width = text[index] === "\\" ? 2 : 1;
  return { index: index + width, tail: nextTail(tail, text[index]) };
}

export function regions(text) {
  const found = [];
  let index = 0;
  let tail = "";
  while (index < text.length) {
    const opened = opener(text, index);
    if (opened === null) {
      const step = codeStep(text, index, tail);
      index = step.index;
      tail = step.tail;
      continue;
    }
    const end = regionEnd(text, index, opened);
    found.push({ index, end, comment: opened.comment });
    index = end;
    tail = opened.comment ? tail : opened.end;
  }
  return found;
}

export function withoutStringsAndComments(text) {
  let out = "";
  let cursor = 0;
  for (const region of regions(text)) {
    out += text.slice(cursor, region.index);
    out += text.slice(region.index, region.end).replace(/[^\n]/g, " ");
    cursor = region.end;
  }
  return out + text.slice(cursor);
}
