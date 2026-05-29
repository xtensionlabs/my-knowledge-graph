import { NextResponse } from "next/server";
import { GatewayError, getNodeDetail } from "@/lib/api";

/**
 * Client → Next route handler → Synapse gateway.
 *
 * The GraphCanvas click handler can't call the gateway directly because the
 * API key lives server-side only. This handler runs in the Next.js process,
 * forwards the request with the key, and returns the JSON to the browser.
 */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  try {
    const data = await getNodeDetail(id);
    return NextResponse.json(data);
  } catch (err) {
    const status = err instanceof GatewayError ? err.status : 502;
    const message = err instanceof Error ? err.message : "unknown error";
    return NextResponse.json({ error: message }, { status });
  }
}
