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
  /\b(?:await|case|delete|do|else|export|extends|from|import|in|instanceof|new|of|return|throw|typeof|void|yield)$/;

export function lineOf(text, index) {
  return text.slice(0, index).split("\n").length;
}

const QUOTES = new Set(['"', "'"]);

function insideAWord(text, index) {
  const before = text.slice(Math.max(0, index - TAIL_LENGTH), index);
  return /\w$/.test(before) && !KEYWORD_FOLLOWS.test(before);
}

function opener(text, index) {
  if (QUOTES.has(text[index]) && insideAWord(text, index)) return null;
  return REGIONS.find((region) => text.startsWith(region.start, index)) ?? null;
}

function interpolationEnd(text, index) {
  let depth = 1;
  let cursor = index;
  while (cursor < text.length) {
    const opened = opener(text, cursor);
    if (opened !== null) {
      cursor = regionEnd(text, cursor, opened).end;
      continue;
    }
    if (text[cursor] === "{") depth++;
    else if (text[cursor] === "}" && --depth === 0) return cursor;
    cursor++;
  }
  return text.length;
}

function regionEnd(text, index, opened) {
  const holes = [];
  let cursor = index + opened.start.length;
  while (cursor < text.length) {
    if (opened.escapes && text[cursor] === "\\") cursor += 2;
    else if (opened.start === "`" && text.startsWith("${", cursor)) {
      const close = interpolationEnd(text, cursor + 2);
      holes.push({ start: cursor + 2, end: close });
      cursor = close + 1;
    } else if (text.startsWith(opened.end, cursor)) {
      return { end: cursor + opened.end.length, holes };
    } else cursor++;
  }
  return { end: text.length, holes };
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

export function balanced(text, open, close) {
  const opening = text[open];
  let depth = 0;
  for (let index = open; index < text.length; index++) {
    if (text[index] === opening) depth++;
    else if (text[index] === close && --depth === 0) return text.slice(open, index);
  }
  return text.slice(open);
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
    const { end, holes } = regionEnd(text, index, opened);
    found.push({ index, end, holes, comment: opened.comment });
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
