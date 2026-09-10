"use client";
import { useState } from "react";
import { Page, Attempt } from "../../lib/prediction";
import { useData, LoadState, Pager, AttemptTable } from "./shared";
export function PredictionTrades() {
  const [filters, setFilters] = useState({
    scenario: "",
    direction: "",
    result: "",
    date: "",
    strategy_version: "",
    sort: "newest",
  });
  const [offset, setOffset] = useState(0);
  const query = new URLSearchParams(
    Object.entries(filters).filter(([, v]) => v),
  );
  query.set("offset", String(offset));
  const state = useData<Page<Attempt>>(`/attempts?${query}`);
  const change = (key: keyof typeof filters, value: string) => {
    setFilters({ ...filters, [key]: value });
    setOffset(0);
  };
  return (
    <>
      <h2>Журнал попыток</h2>
      <p className="predMuted">
        Каждый сценарий — отдельная попытка. Парные записи объединяются по
        signal ID. Net второго сценария показан для сравнения независимо от
        пагинации; «—» означает, что этот сценарий не создал попытку по тому же
        сигналу.
      </p>
      <div className="predFilters">
        {(
          [
            [
              "scenario",
              "Latency",
              [
                ["", "Все"],
                ["100", "100ms"],
                ["250", "250ms"],
              ],
            ],
            [
              "direction",
              "Направление",
              [
                ["", "Все"],
                ["A", "A"],
                ["B", "B"],
              ],
            ],
            [
              "result",
              "Результат",
              [
                ["", "Все"],
                ["success", "Success"],
                ["failed", "Failed (все)"],
                ["one-leg", "One-leg"],
                ["partial", "Partial"],
              ],
            ],
            [
              "sort",
              "Сортировка",
              [
                ["newest", "Новые"],
                ["pnl", "PnL ↓"],
                ["edge", "Edge ↓"],
                ["capture-time", "Capture time ↑"],
              ],
            ],
          ] as [keyof typeof filters, string, string[][]][]
        ).map(([key, label, options]) => (
          <label key={key}>
            {label}
            <select
              value={filters[key]}
              onChange={(e) => change(key, e.target.value)}
            >
              {options.map(([v, text]) => (
                <option key={v} value={v}>
                  {text}
                </option>
              ))}
            </select>
          </label>
        ))}
        <label>
          Дата UTC
          <input
            type="date"
            value={filters.date}
            onChange={(e) => change("date", e.target.value)}
          />
        </label>
        <label>
          Версия
          <input
            placeholder="1.0.0"
            value={filters.strategy_version}
            onChange={(e) => change("strategy_version", e.target.value)}
          />
        </label>
      </div>
      <LoadState {...state} />
      {state.data && (
        <>
          <AttemptTable attempts={state.data.items} />
          <Pager {...state.data} setOffset={setOffset} />
        </>
      )}
    </>
  );
}
