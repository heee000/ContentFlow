export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

export class StaleResponseError extends Error {
  constructor() { super("请求属于旧页面上下文，结果已忽略"); }
}

export async function apiError(response: Response, fallback: string): Promise<ApiError> {
  const payload = await response.json().catch(() => null);
  return new ApiError(response.status, payload?.error?.code || `http_${response.status}`,
    payload?.error?.message || fallback);
}
