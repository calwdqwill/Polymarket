"use client";
import { useState } from "react";
import { Stats, number } from "../../lib/prediction";
import PredictionChart from "../PredictionChart";
import { useData, LoadState, Net, ScenarioCard } from "./shared";
export function PredictionStats() {
  const state = useData<Record<string, Stats>>("/stats");
  const [scenario, setScenario] = useState("both");
  const stats = Object.values(state.data ?? {});
  const visible = stats.filter(
    (s) => scenario === "both" || String(s.scenario_ms) === scenario,
  );
  const plots = (field: "cumulative" | "by_window" | "by_hour") =>
    visible.map((s) => ({
      name: `${s.scenario_ms}ms`,
      color: s.scenario_ms === 100 ? "#55d5b2" : "#b897ff",
      points: s[field],
    }));
  return (
    <>
      <h2>Статистика · все попытки</h2>
      <LoadState {...state} />
      <div className="predGrid">
        {stats.map((s) => (
          <div key={s.scenario_ms}>
            <ScenarioCard stat={s} />
            <section className="predPanel">
              <dl>
                <dt>PnL / attempt $</dt>
                <dd>{number(s.net_per_attempt)}</dd>
                <dt>Median PnL $</dt>
                <dd>{number(s.median_net)}</dd>
                <dt>Worst attempt $</dt>
                <dd>
                  <Net value={s.worst_attempt} />
                </dd>
                <dt>One-leg rate</dt>
                <dd>
                  {s.one_leg_rate == null
                    ? "—"
                    : number(Number(s.one_leg_rate) * 100, 2) + "%"}
                </dd>
                <dt>Two-leg full rate</dt>
                <dd>
                  {s.two_leg_fill_rate == null
                    ? "—"
                    : number(Number(s.two_leg_fill_rate) * 100, 2) + "%"}
                </dd>
                <dt>Leg fill rate</dt>
                <dd>
                  {s.fill_rate == null
                    ? "—"
                    : number(Number(s.fill_rate) * 100, 2) + "%"}
                </dd>
                <dt>Median capture sec</dt>
                <dd>{number(s.median_capture_seconds, 3)}</dd>
              </dl>
              <h3>Причины неудачных попыток</h3>
              {Object.entries(s.failures).map(([reason, count]) => (
                <p key={reason}>
                  {reason}: {count}
                </p>
              ))}
              <details>
                <summary>Причины окон без capture</summary>
                {Object.entries(s.window_failures).map(([reason, count]) => (
                  <p key={reason}>
                    {reason}: {count}
                  </p>
                ))}
              </details>
            </section>
          </div>
        ))}
      </div>
      <p className="predMuted">
        Success = captures / окна; fill rates = доля попыток либо ног; capture
        time от начала окна. Net/hour и captures/hour используют сумму
        длительностей окон, исключённые окна не заполняются. Сценарии не
        складываются.
      </p>
      <div className="predFilters">
        <label>
          Серии
          <select
            value={scenario}
            onChange={(e) => setScenario(e.target.value)}
          >
            <option value="both">100ms и 250ms</option>
            <option value="100">100ms</option>
            <option value="250">250ms</option>
          </select>
        </label>
      </div>
      {(
        [
          ["cumulative", "Накопленный simulated net $"],
          ["by_window", "Simulated PnL по 5m окнам $"],
          ["by_hour", "Simulated PnL по часам UTC $ (неполные часы включены)"],
        ] as const
      ).map(([field, title]) => (
        <section className="predPanel" key={field}>
          <h3>{title}</h3>
          {field === "cumulative" ? (
            <PredictionChart plots={plots(field)} />
          ) : (
            <div className="predGrid">
              {plots(field).map((plot) => (
                <div key={plot.name}>
                  <PredictionChart plots={[plot]} bars />
                </div>
              ))}
            </div>
          )}
        </section>
      ))}
    </>
  );
}
