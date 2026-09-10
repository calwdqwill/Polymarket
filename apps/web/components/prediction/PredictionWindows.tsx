"use client";
import Link from "next/link";
import { useState } from "react";
import { Page, WindowRow, utc, number } from "../../lib/prediction";
import { useData, LoadState, Net, Pager } from "./shared";
export function PredictionWindows() {
  const [scenario, setScenario] = useState("100");
  const [status, setStatus] = useState("");
  const [date, setDate] = useState("");
  const [offset, setOffset] = useState(0);
  const query = new URLSearchParams({
    scenario,
    status,
    date,
    offset: String(offset),
  });
  const state = useData<Page<WindowRow>>(`/windows?${query}`);
  return (
    <>
      <h2>История окон</h2>
      <div className="predFilters">
        <label>
          Сценарий
          <select
            value={scenario}
            onChange={(e) => {
              setScenario(e.target.value);
              setOffset(0);
            }}
          >
            <option>100</option>
            <option>250</option>
          </select>
        </label>
        <label>
          Статус
          <select
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">Все</option>
            <option value="capture">Capture</option>
            <option value="failed">Failed</option>
            <option value="one-leg">One-leg</option>
            <option value="no-signal">Без сигнала</option>
            <option value="invalid">Неполное valid coverage</option>
          </select>
        </label>
        <label>
          Дата UTC
          <input
            type="date"
            value={date}
            onChange={(e) => {
              setDate(e.target.value);
              setOffset(0);
            }}
          />
        </label>
      </div>
      <LoadState {...state} />
      {state.data && (
        <>
          <div className="predPanel predScroll">
            <table>
              <thead>
                <tr>
                  {[
                    "Окно / UTC",
                    "Match",
                    "Попытки",
                    "Capture",
                    "A/B",
                    "Max edge A / B",
                    "Simulated net $",
                    "Capture sec",
                    "Причина",
                  ].map((x) => (
                    <th key={x}>{x}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {state.data.items.map((w) => (
                  <tr key={w.id}>
                    <td>
                      <Link href={`/prediction/windows/${w.id}`}>
                        {utc(w.spec.start)}
                      </Link>
                      <br />
                      <small>до {utc(w.spec.end)}</small>
                    </td>
                    <td>{w.match_quality}</td>
                    <td>{w.attempts.length}</td>
                    <td>{w.target_captured ? "✓" : "—"}</td>
                    <td>{w.selected_directions.join(" / ") || "—"}</td>
                    <td>
                      {number(w.max_edge.A)} / {number(w.max_edge.B)}
                    </td>
                    <td>
                      <Net value={w.simulated_net} />
                    </td>
                    <td>{number(w.capture_seconds, 3)}</td>
                    <td>{w.failure_reason || "TARGET_CAPTURED"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!state.data.total && (
              <p className="predEmpty">Окон по этим фильтрам нет.</p>
            )}
          </div>
          <Pager {...state.data} setOffset={setOffset} />
        </>
      )}
    </>
  );
}
