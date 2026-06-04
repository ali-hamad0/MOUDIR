import { type FormEvent, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { activationApi } from "../api/activation";
import { ApiError } from "../api/client";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { t } from "../i18n";

type Phase = "checking" | "invalid" | "form" | "done";

export default function ActivatePage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const token = params.get("token") ?? "";

  const [phase, setPhase] = useState<Phase>("checking");
  const [email, setEmail] = useState<string | null>(null);
  const [pw, setPw] = useState("");
  const [confirm, setConfirm] = useState("");
  const [show, setShow] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Validate the token on mount via GET /activate.
  useEffect(() => {
    let cancelled = false;
    if (!token) {
      setPhase("invalid");
      return;
    }
    activationApi
      .check(token)
      .then((res) => {
        if (cancelled) return;
        if (res.valid) {
          setEmail(res.email);
          setPhase("form");
        } else setPhase("invalid");
      })
      .catch(() => !cancelled && setPhase("invalid"));
    return () => {
      cancelled = true;
    };
  }, [token]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (pw.length < 8) {
      setError(t.passwordTooShort);
      return;
    }
    if (pw !== confirm) {
      setError(t.passwordsMismatch);
      return;
    }
    setSubmitting(true);
    try {
      await activationApi.activate(token, pw);
      setPhase("done");
    } catch (err) {
      // A token that expired between check and submit comes back 400.
      setError(
        err instanceof ApiError && err.status === 0 ? t.networkError : t.activateInvalid,
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-dvh items-center justify-center bg-background px-4">
      <div className="w-full max-w-md">
        <h1 className="mb-6 text-center font-sans text-3xl font-bold text-primary">
          {t.appName}
        </h1>
        <div className="rounded-2xl border border-border bg-card p-6 shadow-sm">
          <h2 className="mb-4 text-lg font-semibold text-foreground">{t.activateTitle}</h2>

          {phase === "checking" && (
            <p className="text-muted-foreground">{t.activateChecking}</p>
          )}

          {phase === "invalid" && (
            <div className="flex flex-col gap-4">
              <p className="text-destructive">{t.activateInvalid}</p>
            </div>
          )}

          {phase === "form" && (
            <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
              {email && (
                <p className="text-sm text-muted-foreground" dir="ltr">
                  {email}
                </p>
              )}
              <Field
                name="new_password"
                label={t.newPassword}
                type={show ? "text" : "password"}
                autoComplete="new-password"
                value={pw}
                onChange={(e) => setPw(e.target.value)}
                disabled={submitting}
              />
              <Field
                name="confirm_password"
                label={t.confirmPassword}
                type={show ? "text" : "password"}
                autoComplete="new-password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                disabled={submitting}
              />
              <button
                type="button"
                onClick={() => setShow((v) => !v)}
                className="self-start text-sm text-primary"
              >
                {show ? t.hidePassword : t.showPassword}
              </button>
              {error && (
                <p role="alert" className="text-sm text-destructive">
                  {error}
                </p>
              )}
              <Button type="submit" loading={submitting}>
                {t.activateButton}
              </Button>
            </form>
          )}

          {phase === "done" && (
            <div className="flex flex-col gap-4">
              <p className="text-accent">{t.activateSuccess}</p>
              <Button onClick={() => navigate("/login", { replace: true })}>
                {t.goToLogin}
              </Button>
            </div>
          )}
        </div>

        {(phase === "invalid" || phase === "done") && (
          <p className="mt-4 text-center text-sm">
            <Link to="/login" className="text-primary">
              {t.goToLogin}
            </Link>
          </p>
        )}
      </div>
    </main>
  );
}
