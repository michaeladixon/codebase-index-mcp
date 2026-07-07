// Small helpers shared by the TS layer.
export const API_VERSION = "v2";

export interface Retryable {
  attempts: number;
  backoffMs: number;
}

export type ResultStatus = "ok" | "error" | "retry";

export enum LogLevel {
  Debug,
  Info,
  Error,
}

// Sleep helper for retry backoff.
export const sleep = (ms: number): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, ms));

export function backoff(attempt: number, base: number): number {
  return base * 2 ** attempt;
}
