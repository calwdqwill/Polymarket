"use client";
import Link from "next/link";
import { useState } from "react";
import { Page, WindowRow, Summary, utc } from "../../lib/prediction";
import { useData, LoadState, ScenarioCard } from "./shared";
import { PredictionWindow } from "./PredictionWindow";
export function PredictionOverview() {
  const summary = useData<Summary>("/summary");
  const windows = useData<Page<WindowRow>>("/windows?limit=100");
  const [selected, setSelected] = useState("");
  const current = selected || windows.data?.items[0]?.id;
  return (
    <>
      <LoadState {...summary} />
      {summary.data && (
        <>
          <p className="predMuted">
            Dataset: {summary.data.dataset} · Health:{" "}
            {summary.data.health.status} · Стратегия{" "}
            {String(summary.data.config?.config.strategy_id ?? "—")} /{" "}
            {String(summary.data.config?.config.version ?? "—")}
          </p>
          <div className="predGrid">
            {Object.values(summary.data.scenarios).map((s) => (
              <ScenarioCard key={s.scenario_ms} stat={s} />
            ))}
          </div>
          <details>
            <summary>Фиксированный strategy config и hash</summary>
            <pre>{JSON.stringify(summary.data.config, null, 2)}</pre>
          </details>
        </>
      )}
      <LoadState {...windows} />
      {windows.data && (
        <div className="predFilters">
          <label>
            Историческое окно (UTC)
            <select
              value={current ?? ""}
              onChange={(e) => setSelected(e.target.value)}
            >
              {windows.data.items.map((w) => (
                <option key={w.id} value={w.id}>
                  {utc(w.spec.start)} ·{" "}
                  {w.target_captured ? "CAPTURE" : w.failure_reason}
                </option>
              ))}
            </select>
          </label>
          <Link href="/prediction/windows">Все окна →</Link>
        </div>
      )}
      {current ? (
        <PredictionWindow id={current} />
      ) : (
        !windows.loading && (
          <p className="predEmpty">
            Нет локальных окон. Укажите validated dataset на backend.
          </p>
        )
      )}
    </>
  );
}
