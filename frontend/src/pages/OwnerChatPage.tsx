import { useEffect, useRef, useState } from "react";

import { ApiError } from "../api/client";
import { sendOwnerMessage } from "../api/chat";
import { Button } from "../components/Button";
import { t } from "../i18n";

// Agent routing label → Arabic display name for the badge on each Modir bubble.
const AGENT_LABELS: Record<string, string> = {
  order: "الطلبات",
  inventory: "المخزون",
  finance: "المالية",
  customer: "الزباين",
  advisor: "المستشار",
};

interface Message {
  role: "owner" | "modir";
  text: string;
  agent?: string; // only on modir messages — the routing decision
}

// RTL owner chat panel (Task 7.13). Mobile-first (360px), functional not polished.
// Owner bubbles appear on the right (message-start in RTL), Modir on the left.
// Each Modir bubble shows a small badge naming the specialist agent that replied.
export default function OwnerChatPage() {
  const [messages, setMessages] = useState<Message[]>([
    { role: "modir", text: t.chatWelcome, agent: "advisor" },
  ]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Scroll to the latest message after every update.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function handleSend() {
    const text = input.trim();
    if (!text || sending) return;
    setInput("");
    setError(null);
    setMessages((prev) => [...prev, { role: "owner", text }]);
    setSending(true);
    try {
      const data = await sendOwnerMessage(text);
      setMessages((prev) => [
        ...prev,
        { role: "modir", text: data.response, agent: data.agent },
      ]);
    } catch (err) {
      const msg = err instanceof ApiError ? t.chatError : t.chatError;
      setError(msg);
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

  return (
    <div
      dir="rtl"
      className="flex h-[calc(100dvh-4rem)] flex-col lg:h-[calc(100dvh-2rem)]"
    >
      {/* Header */}
      <div className="border-b border-border px-4 py-3">
        <h1 className="text-base font-semibold text-foreground">
          {t.chatTitle}
        </h1>
      </div>

      {/* Message thread */}
      <div className="flex-1 overflow-y-auto px-4 py-4">
        <div className="mx-auto flex max-w-2xl flex-col gap-3">
          {messages.map((msg, i) =>
            msg.role === "owner" ? (
              <div key={i} className="flex justify-start">
                <div className="max-w-[80%] rounded-2xl rounded-tr-sm bg-primary px-4 py-2.5 text-sm text-primary-foreground">
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
                <div className="max-w-[80%] rounded-2xl rounded-tl-sm bg-muted px-4 py-2.5 text-sm text-foreground">
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
              <div className="rounded-2xl rounded-tl-sm bg-muted px-4 py-2.5">
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
      {error && (
        <p
          role="alert"
          className="px-4 py-2 text-center text-sm text-destructive"
        >
          {error}
        </p>
      )}

      {/* Input area */}
      <div className="border-t border-border px-4 py-3">
        <div className="mx-auto flex max-w-2xl items-end gap-2">
          <textarea
            dir="rtl"
            rows={1}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={t.chatPlaceholder}
            disabled={sending}
            className="min-h-[44px] flex-1 resize-none rounded-xl border border-border bg-background px-3 py-2.5 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/50 disabled:opacity-50"
          />
          <Button
            onClick={() => void handleSend()}
            disabled={!input.trim() || sending}
            className="min-h-[44px] shrink-0"
          >
            {t.chatSend}
          </Button>
        </div>
      </div>
    </div>
  );
}
