"use client";
import Link from "next/link";
import { useState } from "react";
import { Detail, Series, utc, number } from "../../lib/prediction";
import PredictionChart, { Plot } from "../PredictionChart";
import { useData, LoadState, Net, AttemptTable } from "./shared";
export function PredictionWindow({ id }: { id: string }) {
  const state = useData<Detail>(`/windows/${encodeURIComponent(id)}`);
  const series = useData<Series>(`/windows/${encodeURIComponent(id)}/series`);
  const [no, setNo] = useState(false);
  const [eventScenario, setEventScenario] = useState("100");
  const d = state.data;
  const points = series.data?.points ?? [];
  const plots = (field: "prices" | "edges", keys: string[]): Plot[] =>
    keys.map((key, index) => {
      let missingSegment = 0;
      const sampled = points.flatMap((p) => {
        if (p[field][key] == null) {
          missingSegment += 1;
          return [];
        }
        return [
          {
            time: p.time,
            value: p[field][key]!,
            segment: p.segment * 10000 + missingSegment,
          },
        ];
      });
      return {
        name: key,
        color: index % 2 ? "#b897ff" : "#55d5b2",
        points: sampled,
      };
    });
  const latest = points[points.length - 1];
  const events = [
    ...(d?.timeline ?? []).filter(
      (e) => String(e.scenario_ms) === eventScenario,
    ),
    ...(series.data?.quality_events ?? []).flatMap((e) => [
      { timestamp: e.start, state: e.reason, monotonic_ns: Number(e.start_ns) },
    ]),
  ];
  return (
    <>
      <LoadState {...state} />
      {d && (
        <>
          <section className="predPanel">
            <small>ИСТОРИЧЕСКОЕ ОКНО · {d.metadata.match_quality}</small>
            <h2>{d.id}</h2>
            <p>
              {utc(d.metadata.start)} → {utc(d.metadata.end)}
            </p>
            <p className="predMuted">
              {d.metadata.market_ids
                .map(([venue, market]) => `${venue}: ${market}`)
                .join(" · ")}
            </p>
          </section>
          <div className="predGrid">
            {["Polymarket", "Limitless"].map((venue) => (
              <section className="predPanel" key={venue}>
                <h3>{venue}</h3>
                <table>
                  <thead>
                    <tr>
                      <th>Outcome</th>
                      <th>Bid</th>
                      <th>Ask</th>
                      <th>Depth ≥ Q10</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {["YES", "NO"].map((outcome) => {
                      const b = series.data?.books[`${venue}:${outcome}`];
                      return (
                        <tr key={outcome}>
                          <td>{outcome}</td>
                          <td title="Bid не записан в historical cache">—</td>
                          <td>{number(b?.ask)}</td>
                          <td>{number(b?.depth, 2)}</td>
                          <td>{b?.status ?? "NO_DATA"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                <small>
                  Последнее историческое состояние: {utc(series.data?.as_of)}.
                  Depth — сохранённый префикс asks; bids отсутствуют.
                </small>
              </section>
            ))}
          </div>
          <div className="predGrid">
            {Object.values(d.scenarios).map((w) => (
              <section className="predPanel" key={w.scenario_ms}>
                <h3>
                  {w.scenario_ms} ms ·{" "}
                  {w.target_captured
                    ? "TARGET_CAPTURED"
                    : w.attempts.length
                      ? "FAILED"
                      : "WAITING (окно закрыто)"}
                </h3>
                <dl>
                  <dt>Simulated net</dt>
                  <dd>
                    <Net value={w.simulated_net} />
                  </dd>
                  <dt>Попытки</dt>
                  <dd>{w.attempts.length}</dd>
                  <dt>Capture от начала, sec</dt>
                  <dd>{number(w.capture_seconds, 3)}</dd>
                  <dt>Valid coverage</dt>
                  <dd>{number(w.valid_coverage_ns / 3e9, 2)}%</dd>
                  <dt>Причина</dt>
                  <dd>{w.failure_reason || "Цель достигнута"}</dd>
                </dl>
              </section>
            ))}
          </div>
          <p className="predMuted">
            Последний sampled edge A / B: {number(latest?.edges.A)} /{" "}
            {number(latest?.edges.B)} · Predicted net A / B:{" "}
            {number(latest?.predicted_net.A)} /{" "}
            {number(latest?.predicted_net.B)}
          </p>
          <LoadState {...series} />
          {series.data?.status === "READY" ? (
            <>
              <section className="predPanel">
                <h3>Probability / ask price · UTC</h3>
                <label>
                  <input
                    type="checkbox"
                    checked={no}
                    onChange={(e) => setNo(e.target.checked)}
                  />{" "}
                  NO вместо YES
                </label>
                <PredictionChart
                  plots={plots("prices", [
                    `Polymarket:${no ? "NO" : "YES"}`,
                    `Limitless:${no ? "NO" : "YES"}`,
                  ])}
                />
              </section>
              <section className="predPanel">
                <h3>Executable gross edge · A / B · UTC</h3>
                <div className="predFilters">
                  <label>
                    События сценария
                    <select
                      value={eventScenario}
                      onChange={(e) => setEventScenario(e.target.value)}
                    >
                      <option value="100">100 ms</option>
                      <option value="250">250 ms</option>
                    </select>
                  </label>
                  <small>
                    S signal · A/F arrival/fill · C capture · X failure ·
                    красные точки: invalid/STALE
                  </small>
                </div>
                <PredictionChart
                  edge
                  plots={plots("edges", ["A", "B"])}
                  events={events}
                />
                <small>
                  A = LL YES + Poly NO; B = Poly YES + LL NO. 601 samples, не
                  экстремумы; точные события сохранены ниже. Линии разорваны
                  через invalid intervals. Edge без собственного depletion,
                  signal edge — в журнале.
                </small>
              </section>
              <details>
                <summary>
                  Точные интервалы invalid / DESYNC (
                  {series.data.quality_events.length})
                </summary>
                <div className="predScroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Начало ISO UTC</th>
                        <th>Конец ISO UTC</th>
                        <th>Причина</th>
                      </tr>
                    </thead>
                    <tbody>
                      {series.data.quality_events.map((e, i) => (
                        <tr key={i}>
                          <td>{e.start}</td>
                          <td>{e.end}</td>
                          <td>{e.reason}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            </>
          ) : (
            !series.loading && (
              <p className="predEmpty">
                График недоступен: historical cache отсутствует. Журнал доступен
                независимо.
              </p>
            )
          )}
          <h3>Попытки</h3>
          <AttemptTable attempts={d.attempts} />
          <details open>
            <summary>
              Точная последовательность событий (ISO timestamps + monotonic ns)
            </summary>
            <div className="predScroll">
              <table>
                <thead>
                  <tr>
                    <th>Timestamp</th>
                    <th>Monotonic ns</th>
                    <th>Сценарий</th>
                    <th>Событие</th>
                  </tr>
                </thead>
                <tbody>
                  {d.timeline.map((e, i) => (
                    <tr key={i}>
                      <td>{e.timestamp}</td>
                      <td>{String(e.monotonic_ns)}</td>
                      <td>{e.scenario_ms} ms</td>
                      <td>
                        <Link
                          href={`/prediction/trades/${encodeURIComponent(e.attempt_id!)}`}
                        >
                          {e.state}
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </>
      )}
    </>
  );
}
