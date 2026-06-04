import { type FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";

import { adminApi, setAdminToken } from "../api/admin";
import { ApiError } from "../api/client";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { t } from "../i18n";

export default function AdminLoginPage() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!email.trim() || !password) return;
    setSubmitting(true);
    try {
      const res = await adminApi.login(email.trim(), password);
      setAdminToken(res.access_token);
      navigate("/admin", { replace: true });
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 0
          ? t.networkError
          : t.invalidCredentials,
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-dvh items-center justify-center bg-background px-4">
      <div className="w-full max-w-md">
        <div className="mb-6 text-center">
          <h1 className="font-sans text-3xl font-bold text-primary">
            {t.adminTitle}
          </h1>
          <p className="mt-1 text-muted-foreground">{t.adminLoginSubtitle}</p>
        </div>
        <form
          onSubmit={onSubmit}
          noValidate
          className="flex flex-col gap-4 rounded-2xl border border-border bg-card p-6 shadow-sm"
        >
          <h2 className="text-lg font-semibold text-foreground">
            {t.adminLoginTitle}
          </h2>
          <Field
            name="email"
            label={t.email}
            type="email"
            inputMode="email"
            autoComplete="email"
            dir="ltr"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={submitting}
          />
          <Field
            name="password"
            label={t.password}
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={submitting}
          />
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <Button type="submit" loading={submitting}>
            {submitting ? t.loggingIn : t.loginButton}
          </Button>
        </form>
      </div>
    </main>
  );
}
