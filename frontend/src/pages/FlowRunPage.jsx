import { useParams, useNavigate } from "react-router-dom";
import RunView from "../components/agents/flow/RunView";

// Standalone route for a flow run, so a memo / link can deep-link straight to
// it. The run view was previously only reachable as tab state inside Workshop
// > Flows, which left an approval-gate memo with nowhere to open to.
export default function FlowRunPage() {
  const { runId } = useParams();
  const navigate = useNavigate();
  return <RunView runId={runId} onClose={() => navigate(-1)} />;
}
