import { PredictionAttempt } from "../../../../components/PredictionDashboard";
export default async function Page({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <PredictionAttempt id={decodeURIComponent(id)} />;
}
