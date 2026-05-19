import type { ReactNode } from "react";
import "../styles/globals.css";

export const metadata = {
  title: "Крипто-дашборд",
  description: "Аналитика 5-минутных свечей BTC, ETH и SOL"
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
