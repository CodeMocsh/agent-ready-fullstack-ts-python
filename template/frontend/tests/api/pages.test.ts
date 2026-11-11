import { expect, test } from "vitest";
import { API_BASE_URL } from "@/api/base";
import { PAGES } from "@/api/parts";
import { pageAtMost } from "@/mocks/store";

const NAMED = /\{[^}]+\}/;

test.each(PAGES.filter((path) => !NAMED.test(path)))(
  "the mock of %s answers one row a page when a page holds one row",
  async (path) => {
    pageAtMost(1);

    const answer: Record<string, unknown> = await (await fetch(`${API_BASE_URL}${path}`)).json();

    const rows = Object.values(answer).filter((value) => Array.isArray(value));
    expect(rows, `${path} answered no list`).toHaveLength(1);
    expect(rows[0], `the mock of ${path} does not page: read pageRows() in it`).toHaveLength(1);
    expect(answer.next, `the mock of ${path} named no next page`).not.toBeNull();
  },
);
