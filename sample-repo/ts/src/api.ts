import { API_VERSION, Retryable, backoff, sleep } from "./helpers";

// HTTP client with retry support.
export class ApiClient {
  private baseUrl: string;
  private retry: Retryable;

  constructor(baseUrl: string, retry: Retryable) {
    this.baseUrl = baseUrl;
    this.retry = retry;
  }

  // GET a JSON resource, retrying on failure.
  async getJson(path: string): Promise<unknown> {
    for (let i = 0; i < this.retry.attempts; i++) {
      try {
        const res = await fetch(`${this.baseUrl}/${API_VERSION}/${path}`);
        if (res.ok) return res.json();
      } catch {
        await sleep(backoff(i, this.retry.backoffMs));
      }
    }
    throw new Error(`giving up on ${path}`);
  }

  async postJson(path: string, body: unknown): Promise<unknown> {
    const res = await fetch(`${this.baseUrl}/${API_VERSION}/${path}`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    return res.json();
  }
}
