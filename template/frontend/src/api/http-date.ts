const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAY = "(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)";
const LONG_DAY = "(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)";
const MONTH = `(${MONTHS.join("|")})`;
const TIME = "(\\d{2}):(\\d{2}):(\\d{2})";
const IMF_FIXDATE = new RegExp(`^${DAY}, (\\d{2}) ${MONTH} (\\d{4}) ${TIME} GMT$`);
const RFC_850 = new RegExp(`^${LONG_DAY}, (\\d{2})-${MONTH}-(\\d{2}) ${TIME} GMT$`);
const ASCTIME = new RegExp(`^${DAY} ${MONTH} ( \\d|\\d{2}) ${TIME} (\\d{4})$`);
const YEARS_AHEAD_A_SHORT_YEAR_MAY_BE = 50;

export class NotAnHttpDate extends Error {
  override readonly name = "NotAnHttpDate";
}

type Six = [string, string, string, string, string, string];

type Parts = {
  year: number;
  month: string;
  day: string;
  hour: string;
  minute: string;
  second: string;
};

function aShortYear(year: string, now: number): number {
  const thisYear = new Date(now).getUTCFullYear();
  const inThisCentury = thisYear - (thisYear % 100) + Number(year);
  return inThisCentury > thisYear + YEARS_AHEAD_A_SHORT_YEAR_MAY_BE
    ? inThisCentury - 100
    : inThisCentury;
}

function six(match: RegExpExecArray): Six {
  const found = match.slice(1);
  if (found.length !== 6 || found.some((group) => group === undefined)) {
    throw new NotAnHttpDate(`"${match.input}" matched a date form, and not each of its parts`);
  }
  return found as Six;
}

function partsOf(said: string, now: number): Parts {
  const imf = IMF_FIXDATE.exec(said);
  if (imf !== null) {
    const [day, month, year, hour, minute, second] = six(imf);
    return { year: Number(year), month, day, hour, minute, second };
  }
  const rfc850 = RFC_850.exec(said);
  if (rfc850 !== null) {
    const [day, month, year, hour, minute, second] = six(rfc850);
    return { year: aShortYear(year, now), month, day, hour, minute, second };
  }
  const asctime = ASCTIME.exec(said);
  if (asctime !== null) {
    const [month, day, hour, minute, second, year] = six(asctime);
    return { year: Number(year), month, day: day.trim(), hour, minute, second };
  }
  throw new NotAnHttpDate(`"${said}" is not an HTTP date`);
}

export function timeOf(said: string, now: number): number {
  const parts = partsOf(said, now);
  const fields = [
    parts.year,
    MONTHS.indexOf(parts.month),
    Number(parts.day),
    Number(parts.hour),
    Number(parts.minute),
    Number(parts.second),
  ] as const;
  const at = new Date(Date.UTC(...fields));
  const read = [
    at.getUTCFullYear(),
    at.getUTCMonth(),
    at.getUTCDate(),
    at.getUTCHours(),
    at.getUTCMinutes(),
    at.getUTCSeconds(),
  ];
  if (read.some((value, index) => value !== fields[index])) {
    throw new NotAnHttpDate(`"${said}" names no moment in time`);
  }
  return at.getTime();
}
