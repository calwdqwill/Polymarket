"use client";

import { useEffect, useRef } from "react";
import {
  ColorType,
  createChart,
  LineStyle,
  Time,
  UTCTimestamp,
} from "lightweight-charts";
import { preciseTime, Transition, ValuePoint } from "../lib/prediction";

export type Plot = {
  name: string;
  color: string;
  points: (ValuePoint & { segment?: number })[];
};
export default function PredictionChart({
  plots,
  events = [],
  edge = false,
  bars = false,
}: {
  plots: Plot[];
  events?: Transition[];
  edge?: boolean;
  bars?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, {
      height: 280,
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "#111722" },
        textColor: "#aebdd0",
      },
      grid: {
        vertLines: { color: "#202b3b" },
        horzLines: { color: "#202b3b" },
      },
      timeScale: { timeVisible: true, secondsVisible: true },
      localization: {
        timeFormatter: (t: Time) =>
          new Date(Number(t) * 1000).toISOString().slice(11, 23) + " UTC",
      },
    });
    let first = true;
    for (const plot of plots) {
      const groups = new Map<number, ValuePoint[]>();
      for (const p of plot.points) {
        const key = p.segment ?? 0;
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key)!.push(p);
      }
      for (const group of groups.values()) {
        const series = bars
          ? chart.addHistogramSeries({
              color: plot.color,
              priceFormat: { type: "price", precision: 4, minMove: 0.0001 },
            })
          : chart.addLineSeries({
              color: plot.color,
              lineWidth: 2,
              lastValueVisible: false,
              priceLineVisible: false,
              priceFormat: { type: "price", precision: 4, minMove: 0.0001 },
              pointMarkersVisible: group.length === 1,
            });
        const unique = new Map(
          group.map((p) => [preciseTime(p.time), Number(p.value)]),
        );
        series.setData(
          [...unique]
            .sort((a, b) => a[0] - b[0])
            .map(([time, value]) => ({
              time: time as UTCTimestamp,
              value,
              ...(bars ? { color: value < 0 ? "#f4778c" : plot.color } : {}),
            })),
        );
        if (edge && first) {
          for (const threshold of [0.1, 0.15])
            series.createPriceLine({
              price: threshold,
              color: "#eeb866",
              lineWidth: 1,
              lineStyle: LineStyle.Dashed,
              axisLabelVisible: true,
              title: threshold === 0.1 ? "entry >10c" : "cap 15c",
            });
          first = false;
        }
      }
    }
    if (events.length) {
      const grouped = new Map<number, string[]>();
      for (const event of events) {
        const t = preciseTime(event.timestamp);
        grouped.set(t, [
          ...(grouped.get(t) ?? []),
          `${event.scenario_ms ?? ""} ${event.state}`,
        ]);
      }
      const anchors = chart.addLineSeries({
        color: "transparent",
        lineVisible: false,
        lastValueVisible: false,
        priceLineVisible: false,
      });
      const ordered = [...grouped].sort((a, b) => a[0] - b[0]);
      anchors.setData(
        ordered.map(([time]) => ({
          time: time as UTCTimestamp,
          value: edge ? 0.1 : 0.5,
        })),
      );
      anchors.setMarkers(
        ordered.map(([time, labels]) => ({
          time: time as UTCTimestamp,
          position: "aboveBar",
          shape: "circle",
          color: labels.some(
            (l) =>
              l.includes("FAILED") ||
              l.includes("INVALID") ||
              l.includes("STALE"),
          )
            ? "#f4778c"
            : "#eeb866",
          text: labels.some((l) => l.includes("TARGET_CAPTURED"))
            ? "C"
            : labels.some((l) => l.includes("FAILED"))
              ? "X"
              : labels.some((l) => l.includes("ARRIVAL"))
                ? "A/F"
                : labels.some((l) => l.includes("SIGNAL_DETECTED"))
                  ? "S"
                  : "",
          size: 0.5,
        })),
      );
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [plots, events, edge, bars]);
  return (
    <>
      <div className="predLegend">
        {plots.map((p) => (
          <span key={p.name} style={{ color: p.color }}>
            {p.name}
          </span>
        ))}
      </div>
      <div ref={ref} aria-label="График, время UTC" />
    </>
  );
}
