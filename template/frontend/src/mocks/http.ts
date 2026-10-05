import { createOpenApiHttp } from "openapi-msw";
import { API_BASE_URL } from "@/api/base";
import type { paths } from "@/api/schema";

export const http = createOpenApiHttp<paths>({ baseUrl: API_BASE_URL });
