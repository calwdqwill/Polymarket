"use client";
import Link from "next/link";
import { Attempt, number } from "../../lib/prediction";
import { useData, LoadState, Net, BookTable } from "./shared";
export function PredictionAttempt({ id }: { id: string }) {
  const state = useData<{
    selected: Attempt;
    scenarios: Record<string, Attempt>;
  }>(`/attempts/${encodeURIComponent(id)}`);
  const a = state.data?.selected;
  return (
    <>
      <LoadState {...state} />
      {a && (
        <>
          <h2>
            Попытка · {a.direction} · Q{a.q}
          </h2>
          <Link href={`/prediction/windows/${a.window_id}`}>
            {a.window_id} → окно
          </Link>
          <section className="predPanel">
            <h3>Signal</h3>
            <p className="predExact">
              {a.signal_time} · monotonic {String(a.signal_ns)}
            </p>
            <dl>
              <dt>Signal edge</dt>
              <dd>{number(a.signal_edge, 6)}</dd>
              <dt>Predicted net</dt>
              <dd>{number(a.predicted_net, 6)}</dd>
              <dt>TTE, sec</dt>
              <dd>{number(a.tte, 6)}</dd>
              <dt>Strategy version</dt>
              <dd>{a.strategy_version}</dd>
            </dl>
            <div className="predGrid">
              {a.signal_books.map((b) => (
                <div key={b.key}>
                  <h3>{b.key} · signal book</h3>
                  <p>Requested gross Q: {a.requests[b.key]}</p>
                  <p>
                    Signal VWAP: {number(a.signal_quotes?.[b.key]?.vwap, 6)} · с
                    учётом предыдущего consumption
                  </p>
                  <BookTable book={b} />
                </div>
              ))}
            </div>
          </section>
          <div className="predGrid">
            {["100", "250"].map((ms) => {
              const peer = state.data!.scenarios[ms];
              return (
                <section className="predPanel" key={ms}>
                  <h2>{ms} ms</h2>
                  {peer ? (
                    <>
                      <h3>{peer.result}</h3>
                      <dl>
                        <dt>Simulated net $</dt>
                        <dd>
                          <Net value={peer.simulated_net} />
                        </dd>
                        <dt>Friction $</dt>
                        <dd>{number(peer.friction, 6)}</dd>
                        <dt>Residual shares</dt>
                        <dd>{number(peer.residual_q, 6)}</dd>
                      </dl>
                      {peer.legs.map((leg) => (
                        <div key={leg.venue}>
                          <h3>
                            {leg.venue} {leg.outcome}
                          </h3>
                          <p className="predExact">
                            Arrival: {leg.arrival_time ?? "не произошёл"}
                          </p>
                          <dl>
                            <dt>Fill</dt>
                            <dd>{leg.status}</dd>
                            <dt>Gross / retained Q</dt>
                            <dd>
                              {number(leg.filled_q, 6)} /{" "}
                              {number(leg.retained_q, 6)}
                            </dd>
                            <dt>VWAP</dt>
                            <dd>{number(leg.vwap, 6)}</dd>
                            <dt>Cash fee $ / contracts fee</dt>
                            <dd>
                              {number(leg.cash_fee, 6)} /{" "}
                              {number(leg.contracts_fee, 6)}
                            </dd>
                            <dt>Причина</dt>
                            <dd>{leg.reason ?? "—"}</dd>
                          </dl>
                          <details>
                            <summary>Arrival book</summary>
                            <BookTable book={leg.arrival_book} />
                          </details>
                          <details>
                            <summary>Исполненная глубина</summary>
                            <BookTable book={{ asks: leg.depth_consumed }} />
                          </details>
                        </div>
                      ))}
                      <h3>State transitions</h3>
                      {peer.transitions.map((t, i) => (
                        <p key={i} className="predExact">
                          {t.timestamp}
                          <br />
                          {t.state} · {String(t.monotonic_ns)} ns
                        </p>
                      ))}
                    </>
                  ) : (
                    <p>
                      Для этого signal ID сценарий не создал попытку. Ближайший
                      по времени сигнал не подставляется.
                    </p>
                  )}
                </section>
              );
            })}
          </div>
        </>
      )}
    </>
  );
}
