import { api } from "./client";

export interface ChatResponse {
  response: string;
  agent: string; // routing decision: order|inventory|finance|customer|advisor
  session_id: string;
}

export async function sendOwnerMessage(text: string): Promise<ChatResponse> {
  return api.post<ChatResponse>("/chat", { message: text });
}
