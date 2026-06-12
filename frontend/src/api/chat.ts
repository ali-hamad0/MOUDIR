import { api } from "./client";

export interface ChatResponse {
  response: string;
  agent: string; // routing decision: order|inventory|finance|customer|advisor
  session_id: string;
}

export interface ChatSession {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  role: "owner" | "modir";
  content: string;
  agent: string | null;
  created_at: string;
}

// Legacy single-thread send (kept for compatibility; the chat page now uses
// saved sessions below).
export async function sendOwnerMessage(text: string): Promise<ChatResponse> {
  return api.post<ChatResponse>("/chat", { message: text });
}

// ── Saved sessions (Phase 10) ────────────────────────────────────────────────

export async function createChatSession(): Promise<ChatSession> {
  return api.post<ChatSession>("/chat/sessions", {});
}

export async function listChatSessions(): Promise<ChatSession[]> {
  return api.get<ChatSession[]>("/chat/sessions");
}

export async function listChatMessages(sessionId: string): Promise<ChatMessage[]> {
  return api.get<ChatMessage[]>(`/chat/sessions/${sessionId}/messages`);
}

export async function sendSessionMessage(
  sessionId: string,
  text: string,
): Promise<ChatResponse> {
  return api.post<ChatResponse>(`/chat/sessions/${sessionId}/messages`, {
    message: text,
  });
}

export interface VoiceChatResponse {
  transcript: string; // what the owner said (their bubble)
  response: string; // Modir's reply text
  agent: string;
  session_id: string;
  audio_b64: string; // the spoken reply, base64 WAV
  audio_mime: string;
}

export async function sendVoiceMessage(
  sessionId: string,
  audio: Blob,
): Promise<VoiceChatResponse> {
  const form = new FormData();
  form.append("audio", audio, "turn.webm");
  return api.upload<VoiceChatResponse>(`/chat/sessions/${sessionId}/voice`, form);
}
