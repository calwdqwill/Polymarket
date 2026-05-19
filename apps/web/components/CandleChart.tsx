"use client";

import { useEffect, useMemo, useRef } from "react";
import {
  ColorType,
  CrosshairMode,
  createChart,
  type CandlestickData,
  type IChartApi,
  type ISeriesApi,
  type SeriesMarker,
  type UTCTimestamp
} from "lightweight-charts";

import type { Candle, ImbalanceEvent } from "../lib/api";
import { parseApiDate } from "../lib/api";

type CandleChartProps = {
  candles: Candle[];
  imbalances: ImbalanceEvent[];
  selectedCandleId: number | null;
  onSelectCandle: (candle: Candle) => void;
};

function toChartTime(value: string): UTCTimestamp {
  return Math.floor(parseApiDate(value).getTime() / 1000) as UTCTimestamp;
}

export function CandleChart({ candles, imbalances, selectedCandleId, onSelectCandle }: CandleChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const candlesRef = useRef<Candle[]>(candles);

  const chartData = useMemo<CandlestickData[]>(() => {
    return candles.map((candle) => ({
      time: toChartTime(candle.timestamp_start),
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close
    }));
  }, [candles]);

  const markers = useMemo<SeriesMarker<UTCTimestamp>[]>(() => {
    const seen = new Set<string>();
    const candleTimes = new Set(candles.map((candle) => toChartTime(candle.timestamp_start)));
    const nextMarkers: SeriesMarker<UTCTimestamp>[] = [];
    const importantEvents = [...imbalances]
      .sort((left, right) => (right.severity ?? 0) - (left.severity ?? 0))
      .slice(0, 36);

    for (const event of importantEvents) {
      const time = toChartTime(event.timestamp_start);
      const key = `${time}-${event.event_type}-${event.direction}`;
      if (seen.has(key) || !candleTimes.has(time)) {
        continue;
      }
      seen.add(key);
      const isBullish = event.direction === "bullish";
      nextMarkers.push({
        time,
        position: isBullish ? "belowBar" : "aboveBar",
        shape: isBullish ? "arrowUp" : "arrowDown",
        color: isBullish ? "#23c6a7" : "#f06a8a",
        size: 0.75
      });
      if (nextMarkers.length >= 36) {
        break;
      }
    }

    return nextMarkers.sort((left, right) => Number(left.time) - Number(right.time));
  }, [candles, imbalances]);

  useEffect(() => {
    candlesRef.current = candles;
  }, [candles]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return;
    }

    const chart = createChart(container, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#aeb8c7",
        fontFamily: "Inter, ui-sans-serif, system-ui"
      },
      grid: {
        vertLines: { color: "rgba(142, 160, 182, 0.08)" },
        horzLines: { color: "rgba(142, 160, 182, 0.08)" }
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: "rgba(154, 166, 188, 0.35)" },
        horzLine: { color: "rgba(154, 166, 188, 0.35)" }
      },
      rightPriceScale: {
        borderColor: "rgba(142, 160, 182, 0.16)"
      },
      timeScale: {
        borderColor: "rgba(142, 160, 182, 0.16)",
        timeVisible: true,
        secondsVisible: false
      },
      handleScroll: true,
      handleScale: true
    });

    const series = chart.addCandlestickSeries({
      upColor: "#20c997",
      downColor: "#f05f7f",
      borderUpColor: "#20c997",
      borderDownColor: "#f05f7f",
      wickUpColor: "#20c997",
      wickDownColor: "#f05f7f"
    });

    chart.subscribeClick((param) => {
      if (!param.time) {
        return;
      }
      const clickedTime = Number(param.time);
      const matched = candlesRef.current.find((candle) => Number(toChartTime(candle.timestamp_start)) === clickedTime);
      if (matched) {
        onSelectCandle(matched);
      }
    });

    chartRef.current = chart;
    seriesRef.current = series;

    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [onSelectCandle]);

  useEffect(() => {
    const chart = chartRef.current;
    const series = seriesRef.current;
    if (!chart || !series) {
      return;
    }

    series.setData(chartData);
    series.setMarkers(markers);
    if (chartData.length > 0) {
      chart.timeScale().fitContent();
    }
  }, [chartData, markers]);

  return (
    <div className="chartShell">
      <div className="chartCanvas" ref={containerRef} />
      {chartData.length === 0 ? (
        <div className="emptyOverlay">
          <strong>Нет свечей</strong>
          <span>Проверь источник данных и период.</span>
        </div>
      ) : null}
      {selectedCandleId ? <span className="chartSelection">Candle #{selectedCandleId}</span> : null}
    </div>
  );
}
