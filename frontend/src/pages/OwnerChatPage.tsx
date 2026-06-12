import { useEffect, useRef, useState } from "react";

import { Link } from "react-router-dom";

import {
  ChatSession,
  createChatSession,
  listChatMessages,
  listChatSessions,
  sendSessionMessage,
  sendVoiceMessage,
} from "../api/chat";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/context";
import { Button } from "../components/Button";
import { LockIcon, MicIcon, StopIcon } from "../components/icons";
import { lang, t } from "../i18n";

// Agent routing label → display name for the badge on each Modir bubble.
const AGENT_LABELS: Record<string, string> = {
  order: t.agentOrder,
  inventory: t.agentInventory,
  finance: t.agentFinance,
  customer: t.agentCustomer,
  advisor: t.agentAdvisor,
};

interface Message {
  role: "owner" | "modir";
  text: string;
  agent?: string; // only on modir messages — the routing decision
}

// RTL owner chat panel (Task 7.13; saved sessions in Phase 10). Conversations
// persist server-side: the sidebar lists this user's sessions (most recent
// first), selecting one loads its history, and a new session is created lazily
// on the first message so empty chats never pile up.
export default function OwnerChatPage() {
  const { me } = useAuth();
  // Voice is Pro-only; the daily message quota only bites on Free (plan_gate).
  const isPro = me?.effective_plan === "pro";
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  // Mobile-only sessions drawer (the sidebar is always visible at sm+).
  const [sessionsOpen, setSessionsOpen] = useState(false);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [recording, setRecording] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);

  // Scroll to the latest message after every update.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Load the session list once; resume the most recent conversation.
  useEffect(() => {
    void (async () => {
      try {
        const rows = await listChatSessions();
        setSessions(rows);
        if (rows.length > 0) await openSession(rows[0].id);
      } catch {
        setError(t.chatHistoryError);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function openSession(id: string) {
    setActiveId(id);
    setSessionsOpen(false);
    setError(null);
    setLoadingHistory(true);
    try {
      const history = await listChatMessages(id);
      setMessages(
        history.map((m) => ({
          role: m.role,
          text: m.content,
          agent: m.agent ?? undefined,
        })),
      );
    } catch {
      setError(t.chatHistoryError);
    } finally {
      setLoadingHistory(false);
    }
  }

  function startNewChat() {
    // Lazy: the session row is created on the first send.
    setActiveId(null);
    setSessionsOpen(false);
    setMessages([]);
    setError(null);
  }

  async function handleSend() {
    const text = input.trim();
    if (!text || sending) return;
    setInput("");
    setError(null);
    setMessages((prev) => [...prev, { role: "owner", text }]);
    setSending(true);
    try {
      let sessionId = activeId;
      if (!sessionId) {
        const created = await createChatSession();
        sessionId = created.id;
        setActiveId(sessionId);
      }
      const data = await sendSessionMessage(sessionId, text);
      setMessages((prev) => [
        ...prev,
        { role: "modir", text: data.response, agent: data.agent },
      ]);
      // Refresh the sidebar: titles and last-activity ordering live server-side.
      setSessions(await listChatSessions());
    } catch (e) {
      // 402 = the Free daily quota (plan_gate) — show the upgrade nudge.
      setError(e instanceof ApiError && e.status === 402 ? "limit:chat" : t.chatError);
    } finally {
      setSending(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Send on Enter (without Shift). Shift+Enter inserts a newline.
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSend();
    }
  }

  // ── Push-to-talk (Phase 10 voice chat) ──────────────────────────────────
  // Tap to record, tap again to stop & send. The clip goes to the voice
  // endpoint: transcribed server-side (same Gemini STT as WhatsApp voice
  // notes), answered by the supervisor, and the reply comes back as text +
  // spoken WAV that auto-plays.
  async function toggleRecording() {
    if (recording) {
      recorderRef.current?.stop();
      return;
    }
    setError(null);
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setError(t.chatMicError);
      return;
    }
    const recorder = new MediaRecorder(stream, { mimeType: "audio/webm" });
    const chunks: Blob[] = [];
    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunks.push(e.data);
    };
    recorder.onstop = () => {
      // Release the mic immediately; then ship the clip.
      stream.getTracks().forEach((track) => track.stop());
      setRecording(false);
      void sendVoiceTurn(new Blob(chunks, { type: "audio/webm" }));
    };
    recorderRef.current = recorder;
    recorder.start();
    setRecording(true);
  }

  async function sendVoiceTurn(clip: Blob) {
    if (clip.size === 0) return;
    setSending(true);
    setError(null);
    try {
      let sessionId = activeId;
      if (!sessionId) {
        const created = await createChatSession();
        sessionId = created.id;
        setActiveId(sessionId);
      }
      const data = await sendVoiceMessage(sessionId, clip);
      setMessages((prev) => [
        ...prev,
        { role: "owner", text: data.transcript },
        { role: "modir", text: data.response, agent: data.agent },
      ]);
      setSessions(await listChatSessions());
      // Speak the reply.
      const audio = new Audio(`data:${data.audio_mime};base64,${data.audio_b64}`);
      void audio.play().catch(() => {
        /* autoplay blocked — the reply is still shown as text */
      });
    } catch {
      setError(t.chatVoiceError);
    } finally {
      setSending(false);
    }
  }

  // One session list, two homes: the always-visible sidebar at sm+ and the
  // slide-in drawer on phones (a fixed 144px sidebar squeezed the thread to
  // ~230px on a 375px screen — ux content-priority).
  const sessionList = (
    <>
      <div className="border-b border-border p-2">
        <Button onClick={startNewChat} className="w-full text-sm">
          {t.chatNewSession}
        </Button>
      </div>
      <p className="px-3 pb-1 pt-3 text-xs font-semibold text-muted-foreground">
        {t.chatSessionsTitle}
      </p>
      <div className="flex-1 overflow-y-auto px-2 pb-2">
        {sessions.map((s) => (
          <button
            key={s.id}
            onClick={() => void openSession(s.id)}
            className={`mb-1 min-h-[44px] w-full truncate rounded-lg px-3 py-2 text-start text-sm ${
              s.id === activeId
                ? "bg-primary/10 font-medium text-foreground"
                : "text-muted-foreground hover:bg-muted"
            }`}
          >
            {s.title ?? t.chatUntitled}
          </button>
        ))}
      </div>
    </>
  );

  return (
    // Fill exactly the viewport space the shell leaves free (top bar + main
    // paddings + bottom nav), so the composer is always on screen — the old
    // 100dvh-4rem overshot and pushed the input behind the mobile bottom nav.
    <div className="flex h-[calc(100dvh-11.5rem)] lg:h-[calc(100dvh-3.25rem)]">
      {/* Sessions sidebar (sm+) — phones get the drawer below instead. */}
      <aside className="hidden w-56 shrink-0 flex-col border-e border-border sm:flex">
        {sessionList}
      </aside>

      {/* Mobile sessions drawer */}
      {sessionsOpen && (
        <div
          className="fixed inset-0 z-40 bg-foreground/40 sm:hidden"
          role="dialog"
          aria-modal="true"
          aria-label={t.chatSessionsTitle}
          onClick={() => setSessionsOpen(false)}
        >
          <div
            className="flex h-full w-72 max-w-[85vw] flex-col border-e border-border bg-card shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            {sessionList}
          </div>
        </div>
      )}

      {/* Conversation pane */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Header */}
        <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-2">
          <h1 className="text-base font-semibold text-foreground">
            {t.chatTitle}
          </h1>
          <button
            type="button"
            onClick={() => setSessionsOpen(true)}
            className="min-h-[44px] shrink-0 rounded-lg px-3 text-sm font-medium text-primary hover:bg-primary/10 sm:hidden"
          >
            {t.chatSessionsTitle}
          </button>
        </div>

        {/* Message thread */}
        <div className="flex-1 overflow-y-auto px-4 py-4">
          <div className="mx-auto flex max-w-2xl flex-col gap-3">
            {messages.length === 0 && !loadingHistory && (
              <div className="flex flex-col items-end gap-1">
                <span className="px-1 text-xs text-muted-foreground">
                  {AGENT_LABELS.advisor}
                </span>
                <div className="max-w-[80%] rounded-2xl rounded-se-sm bg-muted px-4 py-2.5 text-sm text-foreground">
                  {t.chatWelcome}
                </div>
              </div>
            )}
            {messages.map((msg, i) =>
              msg.role === "owner" ? (
                <div key={i} className="flex justify-start">
                  <div
                    dir="auto"
                    className="max-w-[80%] rounded-2xl rounded-ss-sm bg-primary px-4 py-2.5 text-sm text-on-primary"
                  >
                    {msg.text}
                  </div>
                </div>
              ) : (
                <div key={i} className="flex flex-col items-end gap-1">
                  {msg.agent && (
                    <span className="px-1 text-xs text-muted-foreground">
                      {AGENT_LABELS[msg.agent] ?? msg.agent}
                    </span>
                  )}
                  <div
                    dir="auto"
                    className="max-w-[80%] rounded-2xl rounded-se-sm bg-muted px-4 py-2.5 text-sm text-foreground"
                  >
                    {msg.text}
                  </div>
                </div>
              ),
            )}
            {sending && (
              <div className="flex flex-col items-end gap-1">
                <span className="px-1 text-xs text-muted-foreground">
                  {t.chatSending}
                </span>
                <div className="rounded-2xl rounded-se-sm bg-muted px-4 py-2.5">
                  <span className="inline-flex gap-1">
                    <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground [animation-delay:0ms]" />
                    <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground [animation-delay:150ms]" />
                    <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground [animation-delay:300ms]" />
                  </span>
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>
        </div>

        {/* Error banner */}
        {error === "limit:chat" ? (
          <p role="alert" className="px-4 py-2 text-center text-sm text-status-pending">
            {t.limitChatMsg}{" "}
            <Link to="/upgrade" className="font-medium text-primary underline">
              {t.upgradeCta}
            </Link>
          </p>
        ) : error ? (
          <p
            role="alert"
            className="px-4 py-2 text-center text-sm text-destructive"
          >
            {error}
          </p>
        ) : null}

        {/* Input area */}
        <div className="border-t border-border px-4 py-3">
          <div className="mx-auto flex max-w-2xl items-end gap-2">
            <textarea
              dir={lang === "ar" ? "rtl" : "ltr"}
              rows={1}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={t.chatPlaceholder}
              disabled={sending}
              className="min-h-[44px] flex-1 resize-none rounded-xl border border-border bg-background px-3 py-2.5 text-base text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/50 disabled:opacity-50"
            />
            {isPro ? (
              <Button
                onClick={() => void toggleRecording()}
                disabled={sending}
                className={`min-h-[44px] shrink-0 ${recording ? "animate-pulse" : ""}`}
              >
                {recording ? (
                  <StopIcon className="h-4 w-4" />
                ) : (
                  <MicIcon className="h-4 w-4" />
                )}
                {recording ? t.chatRecordStop : t.chatRecord}
              </Button>
            ) : (
              // Voice is Pro-only: the button becomes the upgrade entry point.
              <Link
                to="/upgrade"
                aria-label={t.proFeatureLockHint}
                title={t.proFeatureLockHint}
                className="flex min-h-[44px] shrink-0 items-center gap-1.5 rounded-lg bg-muted px-3 text-sm font-medium text-muted-foreground hover:text-foreground"
              >
                <LockIcon className="h-4 w-4" />
                {t.chatRecord}
              </Link>
            )}
            <Button
              onClick={() => void handleSend()}
              disabled={!input.trim() || sending || recording}
              className="min-h-[44px] shrink-0"
            >
              {t.chatSend}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
