export type AssetSymbol = "BTC" | "ETH" | "SOL";
export type CandleSource = "binance_klines" | "chainlink_streams" | "chainlink_candlestick";
export type Direction = "bullish" | "bearish" | "neutral";

export type Asset = {
  id: number;
  symbol: AssetSymbol;
  name: string;
  source_config: Record<string, unknown> | null;
};

export type Candle = {
  id: number;
  asset: AssetSymbol;
  timeframe: string;
  timestamp_start: string;
  timestamp_end: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
  tick_count: number;
  first_tick_time: string | null;
  last_tick_time: string | null;
  color: "green" | "red" | "neutral" | null;
  source: CandleSource | string;
  created_at: string | null;
  updated_at: string | null;
};

export type WindowMetrics = {
  timestamp_start: string;
  timestamp_end: string;
  percentage_change: number;
  absolute_change: number;
  max_high: number;
  min_low: number;
  range_percent: number;
  green_count: number;
  red_count: number;
  neutral_count: number;
  average_body: number;
  average_upper_wick: number;
  average_lower_wick: number;
  average_body_to_range_ratio: number;
};

export type WindowAnalysis = {
  asset: AssetSymbol;
  timeframe: string;
  source: CandleSource | string | null;
  window: number;
  candles_loaded: number;
  windows: WindowMetrics[];
};

export type ImbalanceEvent = {
  id: number;
  asset: AssetSymbol;
  timeframe: string;
  source: CandleSource | string | null;
  window_size: number;
  timestamp_start: string;
  timestamp_end: string;
  direction: Direction;
  percent_change: number | null;
  z_score: number | null;
  severity: number | null;
  event_type: string;
  metadata: Record<string, unknown> | null;
  created_at: string | null;
};

export type Tick = {
  id: number;
  asset: AssetSymbol;
  timestamp: string;
  price: number;
  source: string;
  raw_payload: Record<string, unknown> | null;
  created_at: string | null;
};

export type CandleDrilldown = {
  candle: Candle;
  features: Record<string, string>;
  ticks: Tick[];
  tick_count: number;
  has_realtime_ticks: boolean;
};

export type SourceAssetHealth = {
  candles: number;
  ticks: number;
  first_candle: string | null;
  last_candle: string | null;
  last_tick: string | null;
  last_candle_age_seconds: number | null;
  last_tick_age_seconds: number | null;
};

export type SourceRunHealth = {
  id: number;
  source: string;
  job_type: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  checkpoint: Record<string, unknown> | null;
};

export type SourceHealthItem = {
  source: CandleSource | string;
  label: string;
  kind: string;
  notes: string[];
  total_candles: number;
  total_ticks: number;
  first_candle: string | null;
  last_candle: string | null;
  last_tick: string | null;
  last_candle_age_seconds: number | null;
  last_tick_age_seconds: number | null;
  is_live: boolean;
  blocked_reason: string | null;
  latest_run: SourceRunHealth | null;
  assets: Record<AssetSymbol, SourceAssetHealth>;
};

export type SourceHealth = {
  timeframe: string;
  generated_at: string;
  sources: SourceHealthItem[];
};

export type PeriodKey = "1d" | "3d" | "7d" | "14d";

export const ASSETS: AssetSymbol[] = ["BTC", "ETH", "SOL"];

export const SOURCES: Array<{ value: CandleSource; label: string; note: string }> = [
  { value: "binance_klines", label: "Binance historical", note: "90d fallback" },
  { value: "chainlink_streams", label: "Chainlink accumulated realtime", note: "local worker" },
  { value: "chainlink_candlestick", label: "Chainlink Candlestick API", note: "requires auth" }
];

export const PERIODS: Array<{ value: PeriodKey; label: string; days: number; limit: number }> = [
  { value: "1d", label: "1D", days: 1, limit: 400 },
  { value: "3d", label: "3D", days: 3, limit: 1000 },
  { value: "7d", label: "7D", days: 7, limit: 2200 },
  { value: "14d", label: "14D", days: 14, limit: 4300 }
];

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

type QueryValue = string | number | boolean | null | undefined;

async function request<T>(path: string, query: Record<string, QueryValue> = {}): Promise<T> {
  const url = new URL(path, API_BASE_URL);
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, String(value));
    }
  });

  const response = await fetch(url.toString(), {
    headers: { Accept: "application/json" }
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`API ${response.status}: ${detail || response.statusText}`);
  }

  return response.json() as Promise<T>;
}

export function getPeriodRange(period: PeriodKey): { from: string; to: string; limit: number } {
  const preset = PERIODS.find((item) => item.value === period) ?? PERIODS[2];
  const to = new Date();
  const from = new Date(to);
  from.setUTCDate(to.getUTCDate() - preset.days);
  return { from: from.toISOString(), to: to.toISOString(), limit: preset.limit };
}

export function parseApiDate(value: string): Date {
  if (/[zZ]$|[+-]\d{2}:\d{2}$/.test(value)) {
    return new Date(value);
  }
  return new Date(`${value}Z`);
}

export function fetchAssets(): Promise<Asset[]> {
  return request<Asset[]>("/api/assets");
}

export function fetchSourceHealth(): Promise<SourceHealth> {
  return request<SourceHealth>("/api/status/sources", {
    timeframe: "5m"
  });
}

export function fetchCandles(params: {
  asset: AssetSymbol;
  source: CandleSource;
  from: string;
  to: string;
  limit: number;
}): Promise<Candle[]> {
  return request<Candle[]>("/api/candles", {
    asset: params.asset,
    timeframe: "5m",
    source: params.source,
    from: params.from,
    to: params.to,
    limit: params.limit
  });
}

export function fetchWindowAnalysis(params: {
  asset: AssetSymbol;
  source: CandleSource;
  window: number;
  from: string;
  to: string;
  limit: number;
}): Promise<WindowAnalysis> {
  return request<WindowAnalysis>("/api/analysis/window", {
    asset: params.asset,
    timeframe: "5m",
    source: params.source,
    window: params.window,
    from: params.from,
    to: params.to,
    limit: params.limit
  });
}

export function fetchImbalances(params: {
  asset?: AssetSymbol;
  source: CandleSource;
  direction?: Direction;
  eventType?: string;
  minSeverity?: number;
  windowSize?: number;
  from: string;
  to: string;
  limit: number;
}): Promise<ImbalanceEvent[]> {
  return request<ImbalanceEvent[]>("/api/imbalances", {
    asset: params.asset,
    timeframe: "5m",
    source: params.source,
    direction: params.direction,
    event_type: params.eventType,
    min_severity: params.minSeverity,
    window_size: params.windowSize,
    from: params.from,
    to: params.to,
    limit: params.limit
  });
}

export function fetchCandleDrilldown(candleId: number): Promise<CandleDrilldown> {
  return request<CandleDrilldown>(`/api/candles/${candleId}/drilldown`);
}
