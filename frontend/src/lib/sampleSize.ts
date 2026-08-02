/**
 * Small-sample honesty helpers.
 *
 * A percentage from a handful of data points is misleading on its own —
 * "38% win rate" reads very differently from "38% win rate — from 8 deals".
 * These helpers make the sample size visible so a rep never over-trusts a
 * rate computed from almost nothing.
 */

/** Below this many data points, a rate must be shown with its sample size. */
export const SMALL_SAMPLE_THRESHOLD = 30;

/**
 * Format a rate with its sample size when the sample is small.
 *
 * @param percent Whole-number percentage, e.g. 38.
 * @param sampleSize How many data points the percentage was computed from.
 * @param unit Plural noun for the sample, e.g. "deals", "emails".
 * @returns e.g. "38% — from 8 deals" (small sample) or "38%" (large enough).
 */
export function formatRate(percent: number, sampleSize: number, unit: string): string {
  if (sampleSize < SMALL_SAMPLE_THRESHOLD) {
    return `${percent}% — from ${sampleSize} ${unit}`;
  }
  return `${percent}%`;
}

/** Whether a sample is too small to trust a percentage from it alone. */
export function isSmallSample(sampleSize: number): boolean {
  return sampleSize < SMALL_SAMPLE_THRESHOLD;
}
