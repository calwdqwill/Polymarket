export type Book = { key: string; asks: [string, string][]; status: string };
export type Transition = {
  timestamp: string;
  state: string;
  monotonic_ns: number;
  scenario_ms?: number;
  attempt_id?: string;
};
export type Leg = {
  venue: string;
  outcome: string;
  arrival_time: string | null;
  arrival_book: Book | null;
  status: string;
  reason: string | null;
  requested_q: string;
  filled_q: string;
  retained_q: string;
  vwap: string | null;
  cash_fee: string;
  contracts_fee: string;
  depth_consumed: [string, string][];
};
export type Attempt = {
  attempt_id: string;
  window_id: string;
  signal_id: string;
  signal_time: string;
  signal_ns: number;
  direction: string;
  q: string;
  signal_edge: string;
  predicted_net: string;
  tte: string;
  scenario_ms: number;
  strategy_version: string;
  signal_books: Book[];
  signal_quotes?: Record<
    string,
    { vwap: string | null; requested_q: string; status: string }
  >;
  peer_nets?: Record<string, string>;
  requests: Record<string, string>;
  legs: Leg[];
  transitions: Transition[];
  simulated_net: string;
  residual_q: string;
  friction: string;
  result: string;
  state: string;
  category: string;
};
export type WindowRow = {
  id: string;
  spec: {
    start: string;
    end: string;
    canonical_market_id: string;
    market_ids: [string, string][];
    match_quality: string;
  };
  scenario_ms: number;
  attempts: string[];
  target_captured: boolean;
  selected_directions: string[];
  max_edge: Record<string, string | null>;
  simulated_net: string;
  capture_seconds: string | null;
  capture_timestamp: string | null;
  failure_reason: string;
  valid_coverage_ns: number;
  match_quality: string;
};
export type Detail = {
  id: string;
  metadata: WindowRow["spec"];
  scenarios: Record<string, WindowRow>;
  attempts: Attempt[];
  timeline: Transition[];
};
export type Point = {
  time: string;
  segment: number;
  valid: boolean;
  prices: Record<string, string | null>;
  edges: Record<string, string>;
  predicted_net: Record<string, string>;
};
export type Series = {
  status: string;
  points: Point[];
  quality_events: {
    start: string;
    end: string;
    reason: string;
    start_ns: string;
    end_ns: string;
  }[];
  books: Record<
    string,
    {
      ask: string | null;
      bid: string | null;
      depth: string | null;
      status: string;
    }
  >;
  as_of?: string;
  sampling?: string;
};
export type ValuePoint = { time: string | number; value: string };
export type Stats = {
  scenario_ms: number;
  windows: number;
  attempts: number;
  captures: number;
  monitored_hours: string;
  valid_hours: string;
  success_rate: string | null;
  captures_per_hour: string | null;
  simulated_net: string;
  net_per_hour: string | null;
  net_per_attempt: string | null;
  median_net: string | null;
  worst_attempt: string | null;
  one_leg_rate: string | null;
  two_leg_fill_rate: string | null;
  fill_rate: string | null;
  median_capture_seconds: string | null;
  failures: Record<string, number>;
  window_failures: Record<string, number>;
  cumulative: ValuePoint[];
  by_window: ValuePoint[];
  by_hour: ValuePoint[];
};
export type Summary = {
  health: { status: string };
  dataset: string;
  config: {
    config: Record<string, string | number | unknown[]>;
    config_hash: string;
  } | null;
  scenarios: Record<string, Stats>;
};
export type Page<T> = {
  total: number;
  offset: number;
  limit: number;
  items: T[];
};

export async function predictionFetch<T>(
  path: string,
  signal?: AbortSignal,
): Promise<T> {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";
  const response = await fetch(`${base}/api/prediction${path}`, {
    signal,
    cache: "no-store",
  });
  if (!response.ok)
    throw new Error(
      `Prediction API ${response.status}: ${await response.text()}`,
    );
  return response.json();
}
export const number = (
  value: string | number | null | undefined,
  digits = 4,
) =>
  value == null
    ? "—"
    : Number(value).toLocaleString("en-US", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      });
export const utc = (value: string | null | undefined) =>
  value
    ? new Date(value).toISOString().replace("T", " ").replace("Z", " UTC")
    : "—";
export function preciseTime(value: string | number): number {
  if (typeof value === "number") return value;
  const fraction = value.match(/\.(\d+)/)?.[1] ?? "";
  return (
    Date.parse(value) / 1000 + Number(`0.${fraction.slice(3) || "0"}`) / 1000
  );
}
