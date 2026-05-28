import { NextResponse } from "next/server";
import { GatewayError, triggerLibrarian } from "@/lib/api";

/**
 * Client → Next.js Route Handler → Synapse gateway.
 *
 * The Inbox panel button can't call the gateway directly because it would
 * need the API key in the browser bundle. This handler runs server-side,
 * carries the key from process env, and returns the agent result to the UI.
 */
export async function POST() {
  try {
    const result = await triggerLibrarian();
    return NextResponse.json(result);
  } catch (err) {
    const status = err instanceof GatewayError ? err.status : 502;
    const message = err instanceof Error ? err.message : "unknown error";
    return NextResponse.json({ ok: false, summary: message }, { status });
  }
}
