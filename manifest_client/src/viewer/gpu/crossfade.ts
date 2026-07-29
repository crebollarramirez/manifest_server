/**
 * Time-based crossfade state for geometry swaps (~300ms, per the seamlessness
 * rules: new geometry fades in over the old; no pop). Pure logic so it is
 * unit-testable; the React layer drives it from useFrame timestamps and
 * disposes the old layer's GPU resources once `done`.
 */

export const CROSSFADE_MS = 300;

export class Crossfade {
  private startedAt: number | null = null;

  constructor(private readonly durationMs: number = CROSSFADE_MS) {}

  begin(nowMs: number): void {
    this.startedAt = nowMs;
  }

  /** 0 -> 1 over the duration; 1 when idle (no fade in progress). */
  progress(nowMs: number): number {
    if (this.startedAt === null) return 1;
    const elapsed = (nowMs - this.startedAt) / this.durationMs;
    return Math.min(1, Math.max(0, elapsed));
  }

  active(nowMs: number): boolean {
    return this.startedAt !== null && this.progress(nowMs) < 1;
  }

  done(nowMs: number): boolean {
    return this.startedAt !== null && this.progress(nowMs) >= 1;
  }

  reset(): void {
    this.startedAt = null;
  }
}
