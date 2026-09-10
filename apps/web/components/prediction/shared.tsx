"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import {
  Attempt,
  number,
  predictionFetch,
  Stats,
  utc,
} from "../../lib/prediction";
export function useData<T>(path: string | null) {
  const [state, setState] = useState<{
    data?: T;
    error?: string;
    loading: boolean;
  }>({ loading: true });
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    setState({ loading: true });
    predictionFetch<T>(path, controller.signal)
      .then((data) => setState({ data, loading: false }))
      .catch((error) => {
        if (!controller.signal.aborted)
          setState({ error: String(error), loading: false });
      });
    return () => controller.abort();
  }, [path]);
  return state;
}
export function LoadState({
  loading,
  error,
}: {
  loading: boolean;
  error?: string;
}) {
  return error ? (
    <p role="alert" className="predWarning">
      {error}
    </p>
  ) : loading ? (
    <p role="status" className="predEmpty">
      Загрузка локальной проекции…
    </p>
  ) : null;
}
export function Net({ value }: { value: string | null }) {
  return (
    <span className={Number(value) < 0 ? "predNegative" : "predPositive"}>
      {number(value)}
    </span>
  );
}
export function Pager({
  total,
  offset,
  setOffset,
}: {
  total: number;
  offset: number;
  setOffset: (n: number) => void;
}) {
  return (
    <div className="predPager">
      <button
        disabled={!offset}
        onClick={() => setOffset(Math.max(0, offset - 25))}
      >
        ← Назад
      </button>
      <span>
        {total ? offset + 1 : 0}–{Math.min(offset + 25, total)} из {total}
      </span>
      <button
        disabled={offset + 25 >= total}
        onClick={() => setOffset(offset + 25)}
      >
        Далее →
      </button>
    </div>
  );
}
export function ScenarioCard({ stat }: { stat: Stats }) {
  return (
    <section className="predPanel">
      <h2>
        {stat.scenario_ms} / {stat.scenario_ms} ms
      </h2>
      <small>Совокупный simulated net, USD</small>
      <div className="predMetric">
        <Net value={stat.simulated_net} />
      </div>
      <dl>
        <dt>Окон / попыток</dt>
        <dd>
          {stat.windows} / {stat.attempts}
        </dd>
        <dt>Captures</dt>
        <dd>
          {stat.captures} ·{" "}
          {stat.success_rate == null
            ? "—"
            : number(Number(stat.success_rate) * 100, 2) + "%"}
        </dd>
        <dt>Net / час</dt>
        <dd>
          <Net value={stat.net_per_hour} />
        </dd>
        <dt>Captures / час</dt>
        <dd>{number(stat.captures_per_hour, 2)}</dd>
        <dt>Календарные / valid часы</dt>
        <dd>
          {number(stat.monitored_hours, 2)} / {number(stat.valid_hours, 2)}
        </dd>
      </dl>
    </section>
  );
}

export function AttemptTable({ attempts }: { attempts: Attempt[] }) {
  // Same signal can have different eligibility across scenarios; never pair by attempt ordinal.
  const groups = new Map<string, Attempt[]>();
  for (const a of attempts) {
    const key = `${a.window_id}:${a.signal_id}:${a.direction}`;
    groups.set(key, [...(groups.get(key) ?? []), a]);
  }
  return (
    <div className="predPanel predScroll">
      <table>
        <thead>
          <tr>
            {[
              "Signal UTC",
              "Window",
              "A/B",
              "Q",
              "Signal edge",
              "100ms net",
              "250ms net",
              "Result",
            ].map((x) => (
              <th key={x}>{x}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {[...groups].map(([key, ats]) => {
            const a = ats[0];
            return (
              <tr key={key}>
                <td>
                  <Link
                    href={`/prediction/trades/${encodeURIComponent(a.attempt_id)}`}
                  >
                    {utc(a.signal_time)}
                  </Link>
                </td>
                <td>
                  <Link href={`/prediction/windows/${a.window_id}`}>
                    {a.window_id}
                  </Link>
                </td>
                <td>{a.direction}</td>
                <td>{a.q}</td>
                <td>{number(a.signal_edge)}</td>
                {[100, 250].map((ms) => (
                  <td key={ms}>
                    <Net
                      value={
                        ats.find((x) => x.scenario_ms === ms)?.simulated_net ??
                        a.peer_nets?.[String(ms)] ??
                        null
                      }
                    />
                  </td>
                ))}
                <td>
                  {ats.map((x) => `${x.scenario_ms}: ${x.result}`).join(" / ")}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {!attempts.length && (
        <p className="predEmpty">
          Попыток нет. Окно без сигнала сохраняется в истории.
        </p>
      )}
    </div>
  );
}

export function BookTable({
  book,
}: {
  book: { asks: [string, string][] } | null;
}) {
  return book ? (
    <table>
      <thead>
        <tr>
          <th>Ask price</th>
          <th>Gross depth</th>
        </tr>
      </thead>
      <tbody>
        {book.asks.map(([p, q]) => (
          <tr key={p}>
            <td>{number(p)}</td>
            <td>{number(q, 6)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  ) : (
    <p>Книга отсутствует</p>
  );
}
