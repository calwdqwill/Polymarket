"use client";

import { useCallback, useEffect, useMemo, useState, type CSSProperties } from "react";

import { CandleChart } from "./CandleChart";
import {
  ASSETS,
  PERIODS,
  SOURCES,
  fetchAssets,
  fetchCandleDrilldown,
  fetchCandles,
  fetchImbalances,
  fetchSourceHealth,
  fetchWindowAnalysis,
  getPeriodRange,
  parseApiDate,
  type Asset,
  type AssetSymbol,
  type Candle,
  type CandleDrilldown,
  type CandleSource,
  type Direction,
  type ImbalanceEvent,
  type PeriodKey,
  type SourceHealth,
  type SourceHealthItem,
  type WindowMetrics
} from "../lib/api";

type DirectionFilter = Direction | "all";

type OverviewItem = {
  candle: Candle | null;
  candlesCount: number;
  eventCount: number;
  periodChange: number | null;
};

const EVENT_TYPE_OPTIONS = [
  "window_percent_move",
  "green_streak",
  "red_streak",
  "body_anomaly",
  "wick_anomaly",
  "body_range_imbalance",
  "rolling_mean_deviation",
  "rolling_median_deviation",
  "z_score_move",
  "percentile_move"
];

const EVENT_LABELS: Record<string, string> = {
  window_percent_move: "Window move",
  green_streak: "Green streak",
  red_streak: "Red streak",
  body_anomaly: "Body anomaly",
  wick_anomaly: "Wick anomaly",
  body_range_imbalance: "Body/range",
  rolling_mean_deviation: "Mean deviation",
  rolling_median_deviation: "Median deviation",
  z_score_move: "Z-score",
  percentile_move: "Percentile"
};

const SOURCE_LABELS: Record<CandleSource, string> = {
  binance_klines: "Binance historical",
  chainlink_streams: "Chainlink accumulated realtime",
  chainlink_candlestick: "Chainlink Candlestick API"
};

const DASHBOARD_GUIDE_SECTIONS = [
  {
    title: "Верхняя карточка",
    items: [
      "Цена - close последней свечи в текущей выборке.",
      "Процент - изменение за выбранный период: (last.close - first.open) / first.open * 100.",
      "Candles - количество загруженных 5m свечей по текущим фильтрам.",
      "Events - количество imbalance events по активу и текущим фильтрам.",
      "Sparkline - быстрый мини-график close без детализации свечей."
    ]
  },
  {
    title: "Источники",
    items: [
      "Binance historical - 90d historical fallback, нужен для полноценной истории.",
      "Chainlink accumulated realtime - локальная история, накопленная только пока запущен worker.",
      "Chainlink Candlestick API - целевой historical Chainlink, сейчас требует рабочую авторизацию.",
      "Source status показывает свежесть worker-а, gaps и причину пустого источника."
    ]
  },
  {
    title: "Фильтры",
    items: [
      "Period задает временное окно: 1D, 3D, 7D или 14D.",
      "Direction оставляет bullish, bearish, neutral или все события.",
      "Event type фильтрует конкретный детектор дисбаланса.",
      "Window задает размер окна в свечах; 12 свечей на 5m примерно равны часу.",
      "Severity отсекает слабые события; смысл значения зависит от типа события."
    ]
  },
  {
    title: "График и события",
    items: [
      "Candlestick chart показывает OHLC-свечи выбранного источника.",
      "Маркеры на графике - самые заметные imbalance events в текущей выборке.",
      "Synced означает, что данные загружены; loading - идет запрос к backend.",
      "Клик по свече открывает ее детали в drill-down."
    ]
  },
  {
    title: "Window metrics",
    items: [
      "Change - движение последнего окна от open первой свечи до close последней.",
      "Range - (max_high - min_low) / first.open * 100.",
      "Max high и Min low - экстремумы внутри окна.",
      "Green / Red - баланс зеленых и красных свечей.",
      "Body/range avg - средняя доля тела свечи в полном диапазоне high-low."
    ]
  },
  {
    title: "Таблицы и heatmap",
    items: [
      "Latest candles показывает последние свечи: Time, Open, High, Low, Close, Ticks.",
      "Ticks важны для Chainlink realtime; historical-источники обычно дают 0 ticks.",
      "Imbalance events показывает Time, Type, Dir, Severity и Move.",
      "Heatmap считает события по активам и типам; яркость - относительная плотность событий."
    ]
  },
  {
    title: "Drill-down и экспорт",
    items: [
      "Drill-down раскрывает признаки выбранной свечи: body, wick, range, ratios и returns.",
      "CSV экспортирует свечи и события текущей выборки в табличном виде.",
      "JSON экспортирует фильтры, candles, imbalances и window metrics."
    ]
  }
];

function formatUsd(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "n/a";
  }
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: value >= 1000 ? 0 : 2
  }).format(value);
}

function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "n/a";
  }
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: digits
  }).format(value);
}

function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "n/a";
  }
  const sign = value > 0 ? "+" : "";
  return `${sign}${formatNumber(value, 2)}%`;
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return "n/a";
  }
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(parseApiDate(value));
}

function formatAge(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) {
    return "n/a";
  }
  if (seconds < 60) {
    return `${seconds}s ago`;
  }
  if (seconds < 3600) {
    return `${Math.floor(seconds / 60)}m ago`;
  }
  return `${Math.floor(seconds / 3600)}h ago`;
}

function sourceHealthMessage(source: CandleSource, health: SourceHealthItem | null, candlesCount: number): string | null {
  if (source === "chainlink_candlestick") {
    if (health?.blocked_reason) {
      return `${health.blocked_reason} This source has ${health.total_candles} candles; use Chainlink accumulated realtime or Binance until Candlestick credentials are fixed.`;
    }
    if (!health || health.total_candles === 0) {
      return "Chainlink Candlestick historical is empty. It is a separate API source, not the locally accumulated realtime stream.";
    }
  }

  if (source === "chainlink_streams") {
    if (!health || health.total_candles === 0) {
      return "Chainlink realtime history is empty. Start poll_chainlink_streams and keep it running to accumulate local 5m candles.";
    }
    if (!health.is_live) {
      return `Chainlink worker is not fresh. Last tick: ${health.last_tick ? formatDateTime(health.last_tick) : "n/a"} (${formatAge(health.last_tick_age_seconds)}).`;
    }
    if (candlesCount < 20) {
      return `Only ${health.total_candles} Chainlink realtime candles are stored. Missed windows cannot be reconstructed from the latest-report endpoint.`;
    }
  }

  return null;
}

function eventLabel(eventType: string): string {
  return EVENT_LABELS[eventType] ?? eventType;
}

function directionLabel(direction: string | null | undefined): string {
  if (direction === "bullish") {
    return "Bullish";
  }
  if (direction === "bearish") {
    return "Bearish";
  }
  return "Neutral";
}

function directionTone(direction: string | null | undefined): "positive" | "negative" | "neutral" {
  if (direction === "bullish") {
    return "positive";
  }
  if (direction === "bearish") {
    return "negative";
  }
  return "neutral";
}

function periodChange(candles: Candle[]): number | null {
  if (candles.length < 2) {
    return null;
  }
  const first = candles[0];
  const last = candles[candles.length - 1];
  return first.open ? ((last.close - first.open) / first.open) * 100 : null;
}

function latestWindow(windows: WindowMetrics[]): WindowMetrics | null {
  return windows.length > 0 ? windows[windows.length - 1] : null;
}

function csvValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "";
  }
  const normalized = typeof value === "object" ? JSON.stringify(value) : String(value);
  return `"${normalized.replaceAll('"', '""')}"`;
}

function downloadFile(filename: string, content: string, mimeType: string) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function Sparkline({ candles }: { candles: Candle[] }) {
  const path = useMemo(() => {
    if (candles.length < 2) {
      return "";
    }
    const width = 280;
    const height = 92;
    const closes = candles.map((candle) => candle.close);
    const min = Math.min(...closes);
    const max = Math.max(...closes);
    const range = max - min || 1;
    return closes
      .map((close, index) => {
        const x = (index / (closes.length - 1)) * width;
        const y = height - ((close - min) / range) * height;
        return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
      })
      .join(" ");
  }, [candles]);

  return (
    <svg className="sparkline" viewBox="0 0 280 96" role="img" aria-label="Мини-график закрытий">
      <path d={path} />
    </svg>
  );
}

export function Dashboard() {
  const [asset, setAsset] = useState<AssetSymbol>("BTC");
  const [source, setSource] = useState<CandleSource>("binance_klines");
  const [period, setPeriod] = useState<PeriodKey>("7d");
  const [windowSize, setWindowSize] = useState(12);
  const [useEventWindow, setUseEventWindow] = useState(false);
  const [direction, setDirection] = useState<DirectionFilter>("all");
  const [eventType, setEventType] = useState("all");
  const [minSeverity, setMinSeverity] = useState(0);

  const [assets, setAssets] = useState<Asset[]>([]);
  const [sourceHealth, setSourceHealth] = useState<SourceHealth | null>(null);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [imbalances, setImbalances] = useState<ImbalanceEvent[]>([]);
  const [heatmapEvents, setHeatmapEvents] = useState<ImbalanceEvent[]>([]);
  const [windowMetrics, setWindowMetrics] = useState<WindowMetrics | null>(null);
  const [overview, setOverview] = useState<Record<AssetSymbol, OverviewItem>>({
    BTC: { candle: null, candlesCount: 0, eventCount: 0, periodChange: null },
    ETH: { candle: null, candlesCount: 0, eventCount: 0, periodChange: null },
    SOL: { candle: null, candlesCount: 0, eventCount: 0, periodChange: null }
  });
  const [selectedCandle, setSelectedCandle] = useState<Candle | null>(null);
  const [drilldown, setDrilldown] = useState<CandleDrilldown | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isDrilldownLoading, setIsDrilldownLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshTick, setRefreshTick] = useState(0);

  const range = useMemo(() => getPeriodRange(period), [period]);

  const selectedSource = SOURCES.find((item) => item.value === source);
  const selectedSourceHealth = sourceHealth?.sources.find((item) => item.source === source) ?? null;
  const latestCandle = candles.length > 0 ? candles[candles.length - 1] : null;
  const selectedAssetName = assets.find((item) => item.symbol === asset)?.name ?? asset;
  const selectedPeriodChange = periodChange(candles);
  const sourceNotice = sourceHealthMessage(source, selectedSourceHealth, candles.length);

  const handleSelectCandle = useCallback((candle: Candle) => {
    setSelectedCandle(candle);
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function loadDashboard() {
      setIsLoading(true);
      setError(null);
      try {
        const eventQuery = {
          source,
          direction: direction === "all" ? undefined : direction,
          eventType: eventType === "all" ? undefined : eventType,
          minSeverity: minSeverity > 0 ? minSeverity : undefined,
          windowSize: useEventWindow ? windowSize : undefined,
          from: range.from,
          to: range.to,
          limit: 500
        };

        const [assetRows, healthRows, candleRows, analysis, selectedEvents, allEvents, ...assetCandleRows] = await Promise.all([
          fetchAssets(),
          fetchSourceHealth(),
          fetchCandles({ asset, source, from: range.from, to: range.to, limit: range.limit }),
          fetchWindowAnalysis({ asset, source, window: windowSize, from: range.from, to: range.to, limit: range.limit }),
          fetchImbalances({ asset, ...eventQuery }),
          fetchImbalances({ source, from: range.from, to: range.to, limit: 1000 }),
          ...ASSETS.map((symbol) => fetchCandles({ asset: symbol, source, from: range.from, to: range.to, limit: range.limit }))
        ]);

        if (cancelled) {
          return;
        }

        const eventCounts = allEvents.reduce(
          (acc, event) => {
            acc[event.asset] += 1;
            return acc;
          },
          { BTC: 0, ETH: 0, SOL: 0 } satisfies Record<AssetSymbol, number>
        );

        const nextOverview = ASSETS.reduce(
          (acc, symbol, index) => {
            const rows = assetCandleRows[index] ?? [];
            acc[symbol] = {
              candle: rows.length > 0 ? rows[rows.length - 1] : null,
              candlesCount: rows.length,
              eventCount: eventCounts[symbol],
              periodChange: periodChange(rows)
            };
            return acc;
          },
          {} as Record<AssetSymbol, OverviewItem>
        );

        setAssets(assetRows);
        setSourceHealth(healthRows);
        setCandles(candleRows);
        setImbalances(selectedEvents);
        setHeatmapEvents(allEvents);
        setWindowMetrics(latestWindow(analysis.windows));
        setOverview(nextOverview);
        setSelectedCandle((previous) => {
          if (previous && candleRows.some((candle) => candle.id === previous.id)) {
            return previous;
          }
          return candleRows.length > 0 ? candleRows[candleRows.length - 1] : null;
        });
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "Не удалось загрузить данные dashboard.");
          setCandles([]);
          setImbalances([]);
          setHeatmapEvents([]);
          setWindowMetrics(null);
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }

    void loadDashboard();

    return () => {
      cancelled = true;
    };
  }, [asset, direction, eventType, minSeverity, range.from, range.limit, range.to, refreshTick, source, useEventWindow, windowSize]);

  useEffect(() => {
    if (source !== "chainlink_streams") {
      return;
    }

    const timer = window.setInterval(() => {
      setRefreshTick((value) => value + 1);
    }, 30_000);

    return () => window.clearInterval(timer);
  }, [source]);

  useEffect(() => {
    let cancelled = false;

    async function loadDrilldown(candle: Candle) {
      setIsDrilldownLoading(true);
      try {
        const data = await fetchCandleDrilldown(candle.id);
        if (!cancelled) {
          setDrilldown(data);
        }
      } catch {
        if (!cancelled) {
          setDrilldown(null);
        }
      } finally {
        if (!cancelled) {
          setIsDrilldownLoading(false);
        }
      }
    }

    if (selectedCandle) {
      void loadDrilldown(selectedCandle);
    } else {
      setDrilldown(null);
    }

    return () => {
      cancelled = true;
    };
  }, [selectedCandle]);

  const heatmap = useMemo(() => {
    const maxCount = Math.max(1, ...heatmapEvents.reduce<number[]>((acc, event) => {
      const keyCount = heatmapEvents.filter((row) => row.asset === event.asset && row.event_type === event.event_type).length;
      acc.push(keyCount);
      return acc;
    }, []));

    return ASSETS.map((symbol) => {
      return {
        asset: symbol,
        cells: EVENT_TYPE_OPTIONS.map((type) => {
          const events = heatmapEvents.filter((event) => event.asset === symbol && event.event_type === type);
          const avgSeverity =
            events.length > 0
              ? events.reduce((sum, event) => sum + (event.severity ?? 0), 0) / events.length
              : 0;
          return {
            type,
            count: events.length,
            avgSeverity,
            intensity: events.length / maxCount
          };
        })
      };
    });
  }, [heatmapEvents]);

  const latestCandles = useMemo(() => {
    return candles.slice(-12).reverse();
  }, [candles]);

  const exportJson = () => {
    const payload = {
      filters: { asset, source, period, windowSize, useEventWindow, direction, eventType, minSeverity },
      candles,
      imbalances,
      window_metrics: windowMetrics
    };
    downloadFile(`poly_crypto_${asset}_${period}.json`, JSON.stringify(payload, null, 2), "application/json;charset=utf-8");
  };

  const exportCsv = () => {
    const rows = [
      ["kind", "asset", "timestamp_start", "timestamp_end", "type", "open", "high", "low", "close", "severity", "direction"],
      ...candles.map((candle) => [
        "candle",
        candle.asset,
        candle.timestamp_start,
        candle.timestamp_end,
        "",
        candle.open,
        candle.high,
        candle.low,
        candle.close,
        "",
        candle.color ?? ""
      ]),
      ...imbalances.map((event) => [
        "imbalance",
        event.asset,
        event.timestamp_start,
        event.timestamp_end,
        event.event_type,
        "",
        "",
        "",
        "",
        event.severity ?? "",
        event.direction
      ])
    ];
    downloadFile(`poly_crypto_${asset}_${period}.csv`, rows.map((row) => row.map(csvValue).join(",")).join("\n"), "text/csv;charset=utf-8");
  };

  return (
    <main className="dashboardShell">
      <aside className="sideRail" aria-label="Навигация">
        <div className="railMark">PC</div>
        <span className="railDot active" />
        <span className="railDot" />
        <span className="railDot" />
      </aside>

      <section className="workspace">
        <header className="appHeader">
          <div>
            <p className="eyebrow">Poly Crypto · phase 8</p>
            <h1>Crypto imbalance dashboard</h1>
          </div>
          <div className="headerActions">
            <button className="ghostButton" type="button" onClick={exportCsv}>
              CSV
            </button>
            <button className="primaryButton" type="button" onClick={exportJson}>
              JSON
            </button>
          </div>
        </header>

        <section className="heroPanel">
          <div className="heroMain">
            <div className="heroMeta">
              <span>{asset} / 5m</span>
              <span>{selectedSource?.note ?? "source"}</span>
              <span className={latestCandle ? "livePill" : "livePill muted"}>{latestCandle ? "data ready" : "no data"}</span>
            </div>
            <p className="heroLabel">{selectedAssetName}</p>
            <div className="heroValue">{formatUsd(latestCandle?.close)}</div>
            <div className="heroBadges">
              <span className={selectedPeriodChange !== null && selectedPeriodChange >= 0 ? "badge positive" : "badge negative"}>
                {formatPercent(selectedPeriodChange)}
              </span>
              <span className="badge">{candles.length} candles</span>
              <span className="badge">{imbalances.length} events</span>
              <span className="badge">{SOURCE_LABELS[source]}</span>
            </div>
          </div>
          <div className="heroSpark">
            <Sparkline candles={candles} />
          </div>
        </section>

        <section className="overviewGrid" aria-label="Обзор активов">
          {ASSETS.map((symbol) => {
            const item = overview[symbol];
            const active = symbol === asset;
            const tone = item.periodChange !== null && item.periodChange >= 0 ? "positive" : "negative";
            return (
              <button
                className={`metricCard ${active ? "active" : ""}`}
                type="button"
                key={symbol}
                onClick={() => setAsset(symbol)}
              >
                <span className="cardKicker">{symbol}</span>
                <strong>{formatUsd(item.candle?.close)}</strong>
                <span className={tone}>{formatPercent(item.periodChange)}</span>
                <small>
                  {item.candlesCount} свечей · {item.eventCount} событий
                </small>
              </button>
            );
          })}
        </section>

        <section className="filtersPanel">
          <div className="segmentedControl" aria-label="Период">
            {PERIODS.map((item) => (
              <button
                className={period === item.value ? "active" : ""}
                type="button"
                key={item.value}
                onClick={() => setPeriod(item.value)}
              >
                {item.label}
              </button>
            ))}
          </div>

          <label className="field">
            <span>Источник</span>
            <select value={source} onChange={(event) => setSource(event.target.value as CandleSource)}>
              {SOURCES.map((item) => (
                <option value={item.value} key={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>

          <label className="field">
            <span>Direction</span>
            <select value={direction} onChange={(event) => setDirection(event.target.value as DirectionFilter)}>
              <option value="all">All</option>
              <option value="bullish">Bullish</option>
              <option value="bearish">Bearish</option>
              <option value="neutral">Neutral</option>
            </select>
          </label>

          <label className="field">
            <span>Event type</span>
            <select value={eventType} onChange={(event) => setEventType(event.target.value)}>
              <option value="all">All events</option>
              {EVENT_TYPE_OPTIONS.map((type) => (
                <option value={type} key={type}>
                  {eventLabel(type)}
                </option>
              ))}
            </select>
          </label>

          <label className="field compact">
            <span>Window</span>
            <input
              min={3}
              max={120}
              type="number"
              value={windowSize}
              onChange={(event) => setWindowSize(Number(event.target.value))}
            />
          </label>

          <label className="toggleField">
            <input
              type="checkbox"
              checked={useEventWindow}
              onChange={(event) => setUseEventWindow(event.target.checked)}
            />
            <span>Event window</span>
          </label>

          <label className="rangeField">
            <span>Severity ≥ {minSeverity}</span>
            <input
              min={0}
              max={10}
              step={0.5}
              type="range"
              value={minSeverity}
              onChange={(event) => setMinSeverity(Number(event.target.value))}
            />
          </label>
        </section>

        {sourceNotice ? (
          <div className="sourceNotice">
            <strong>Source status</strong>
            <span>{sourceNotice}</span>
          </div>
        ) : null}

        <details className="guideDropdown">
          <summary>
            <span>Гайд по индикаторам</span>
            <small>Что означает каждый блок dashboard</small>
          </summary>
          <div className="guideGrid">
            {DASHBOARD_GUIDE_SECTIONS.map((section) => (
              <article className="guideGroup" key={section.title}>
                <h3>{section.title}</h3>
                <ul>
                  {section.items.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </article>
            ))}
          </div>
        </details>

        {error ? <div className="errorBanner">{error}</div> : null}

        <section className="mainGrid">
          <article className="chartPanel">
            <div className="panelHeader">
              <div>
                <p className="eyebrow">Candlestick chart</p>
                <h2>{asset} 5m candles</h2>
              </div>
              <span className={isLoading ? "syncStatus loading" : "syncStatus"}>{isLoading ? "loading" : "synced"}</span>
            </div>
            <CandleChart
              candles={candles}
              imbalances={imbalances}
              selectedCandleId={selectedCandle?.id ?? null}
              onSelectCandle={handleSelectCandle}
            />
          </article>

          <aside className="sidePanel">
            <div className="panelHeader">
              <div>
                <p className="eyebrow">Window metrics</p>
                <h2>{windowSize} candles</h2>
              </div>
            </div>
            <dl className="metricsList">
              <div>
                <dt>Change</dt>
                <dd className={windowMetrics && windowMetrics.percentage_change >= 0 ? "positive" : "negative"}>
                  {formatPercent(windowMetrics?.percentage_change)}
                </dd>
              </div>
              <div>
                <dt>Range</dt>
                <dd>{formatPercent(windowMetrics?.range_percent)}</dd>
              </div>
              <div>
                <dt>Max high</dt>
                <dd>{formatUsd(windowMetrics?.max_high)}</dd>
              </div>
              <div>
                <dt>Min low</dt>
                <dd>{formatUsd(windowMetrics?.min_low)}</dd>
              </div>
              <div>
                <dt>Green / Red</dt>
                <dd>
                  {windowMetrics ? `${windowMetrics.green_count} / ${windowMetrics.red_count}` : "n/a"}
                </dd>
              </div>
              <div>
                <dt>Body/range avg</dt>
                <dd>{formatNumber(windowMetrics?.average_body_to_range_ratio, 3)}</dd>
              </div>
            </dl>
          </aside>
        </section>

        <section className="dataGrid">
          <article className="tablePanel">
            <div className="panelHeader">
              <div>
                <p className="eyebrow">Latest candles</p>
                <h2>Последние свечи</h2>
              </div>
            </div>
            <div className="tableScroll">
              <table>
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Open</th>
                    <th>High</th>
                    <th>Low</th>
                    <th>Close</th>
                    <th>Ticks</th>
                  </tr>
                </thead>
                <tbody>
                  {latestCandles.map((candle) => (
                    <tr
                      className={selectedCandle?.id === candle.id ? "selected" : ""}
                      key={candle.id}
                      onClick={() => setSelectedCandle(candle)}
                    >
                      <td>{formatDateTime(candle.timestamp_start)}</td>
                      <td>{formatUsd(candle.open)}</td>
                      <td>{formatUsd(candle.high)}</td>
                      <td>{formatUsd(candle.low)}</td>
                      <td className={candle.color === "green" ? "positive" : "negative"}>{formatUsd(candle.close)}</td>
                      <td>{candle.tick_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </article>

          <article className="tablePanel">
            <div className="panelHeader">
              <div>
                <p className="eyebrow">Imbalance events</p>
                <h2>События</h2>
              </div>
            </div>
            <div className="tableScroll">
              <table>
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Type</th>
                    <th>Dir</th>
                    <th>Severity</th>
                    <th>Move</th>
                  </tr>
                </thead>
                <tbody>
                  {imbalances.slice(0, 12).map((event) => (
                    <tr key={event.id}>
                      <td>{formatDateTime(event.timestamp_start)}</td>
                      <td>{eventLabel(event.event_type)}</td>
                      <td className={directionTone(event.direction)}>{directionLabel(event.direction)}</td>
                      <td>{formatNumber(event.severity, 2)}</td>
                      <td className={directionTone(event.direction)}>{formatPercent(event.percent_change)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </article>
        </section>

        <section className="bottomGrid">
          <article className="heatmapPanel">
            <div className="panelHeader">
              <div>
                <p className="eyebrow">Heatmap</p>
                <h2>Дисбалансы по активам</h2>
              </div>
            </div>
            <div className="heatmap">
              {heatmap.map((row) => (
                <div className="heatmapRow" key={row.asset}>
                  <span className="heatmapAsset">{row.asset}</span>
                  {row.cells.map((cell) => (
                    <span
                      className="heatCell"
                      key={`${row.asset}-${cell.type}`}
                      title={`${eventLabel(cell.type)}: ${cell.count}`}
                      style={{ "--intensity": cell.intensity.toFixed(2) } as CSSProperties}
                    >
                      <strong>{cell.count}</strong>
                      <small>{cell.avgSeverity ? formatNumber(cell.avgSeverity, 1) : ""}</small>
                    </span>
                  ))}
                </div>
              ))}
            </div>
          </article>

          <article className="drilldownPanel">
            <div className="panelHeader">
              <div>
                <p className="eyebrow">Drill-down</p>
                <h2>{selectedCandle ? `Candle #${selectedCandle.id}` : "Свеча не выбрана"}</h2>
              </div>
              {isDrilldownLoading ? <span className="syncStatus loading">loading</span> : null}
            </div>
            {drilldown ? (
              <div className="drilldownContent">
                <div className="drillSummary">
                  <span>{formatDateTime(drilldown.candle.timestamp_start)}</span>
                  <strong>{formatUsd(drilldown.candle.close)}</strong>
                  <small>{drilldown.tick_count} ticks</small>
                </div>
                <dl className="featureGrid">
                  {Object.entries(drilldown.features).map(([key, value]) => (
                    <div key={key}>
                      <dt>{key.replaceAll("_", " ")}</dt>
                      <dd>{formatNumber(Number(value), key.includes("ratio") ? 3 : 2)}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            ) : (
              <div className="emptyState">Нет drill-down данных для выбранной свечи.</div>
            )}
          </article>
        </section>
      </section>
    </main>
  );
}
