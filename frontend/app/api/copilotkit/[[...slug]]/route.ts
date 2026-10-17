import { CopilotRuntime, createCopilotRuntimeHandler } from "@copilotkit/runtime/v2";
import { HttpAgent } from "@ag-ui/client";

const runtime = new CopilotRuntime({
  agents: {
    default: new HttpAgent({url: `${process.env.DIGGER_API_URL || "http://127.0.0.1:8000"}/agent`}),
  },
});
const handler = createCopilotRuntimeHandler({runtime, basePath: "/api/copilotkit"});
export const GET = handler;
export const POST = handler;
export const PATCH = handler;
export const DELETE = handler;
