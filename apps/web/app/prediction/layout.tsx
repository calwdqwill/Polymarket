import Link from "next/link";
import type { ReactNode } from "react";
import "./prediction.css";

export default function PredictionLayout({
  children,
}: {
  children: ReactNode;
}) {
  return (
    <main className="prediction">
      <header className="predHeader">
        <div>
          <small>LOCAL RESEARCH / HISTORICAL REPLAY</small>
          <h1>Prediction Arb</h1>
        </div>
        <span className="predBadge">READ ONLY · BTC 5m</span>
      </header>
      <nav className="predNav">
        <Link href="/">Свечной дашборд</Link>
        <Link href="/prediction">Обзор</Link>
        <Link href="/prediction/windows">История окон</Link>
        <Link href="/prediction/trades">Журнал попыток</Link>
        <Link href="/prediction/stats">Статистика</Link>
      </nav>
      <p className="predWarning">
        Settlement UNKNOWN · Fees FEE_UNKNOWN. Условный simulated net, не
        реализованная прибыль. Источник — локальная история; реальные ордера и
        live-подключение отсутствуют.
      </p>
      {children}
    </main>
  );
}
