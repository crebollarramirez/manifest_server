import type { ZodError } from "zod";

/**
 * Typed error taxonomy for the transport boundary.
 *
 * - validation / not_found / conflict / backend map from the { error } envelope
 *   plus HTTP status (cad-agent index.ts:1516-1522).
 * - contract_drift marks a Zod parse failure: the backend answered 2xx but the
 *   shape no longer matches CONTRACT.md — a first-class event, surfaced loudly
 *   and recorded as a contract amendment once confirmed.
 * - network covers fetch/transport failures (retry/backoff is TanStack Query's
 *   job, never hand-rolled here).
 *
 * ApiError.message may contain backend/AI-influenced text: render through
 * React's default escaping only; never interpolate into markup or URLs.
 */
export type ApiErrorKind =
  | "validation"
  | "not_found"
  | "conflict"
  | "backend"
  | "contract_drift"
  | "network";

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | undefined;

  constructor(kind: ApiErrorKind, message: string, status?: number) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
  }
}

export function apiErrorFromStatus(status: number, message: string): ApiError {
  if (status === 400) return new ApiError("validation", message, status);
  if (status === 404) return new ApiError("not_found", message, status);
  if (status === 409) return new ApiError("conflict", message, status);
  return new ApiError("backend", message, status);
}

export function contractDriftError(action: string, error: ZodError): ApiError {
  // ZodError details are safe to surface (field paths, not payload contents).
  return new ApiError(
    "contract_drift",
    `Response for "${action}" no longer matches CONTRACT.md: ${error.message}`,
  );
}
