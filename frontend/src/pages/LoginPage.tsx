import { type FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { ApiError } from "../api/client";
import { useAuth } from "../auth/context";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { sanitizePhone } from "../format";
import { t } from "../i18n";

type Touched = { whatsapp_number: boolean; email: boolean; password: boolean };

export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();

  const [whatsapp, setWhatsapp] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [touched, setTouched] = useState<Touched>({
    whatsapp_number: false,
    email: false,
    password: false,
  });
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  // Light per-field "required" validation, shown only after blur (ux
  // inline-validation: validate on blur, not on keystroke).
  const fieldError = (value: string, was: boolean) =>
    was && value.trim() === "" ? t.required : null;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setFormError(null);
    setTouched({ whatsapp_number: true, email: true, password: true });
    if (!whatsapp.trim() || !email.trim() || !password.trim()) return;

    setSubmitting(true);
    try {
      await login({
        whatsapp_number: sanitizePhone(whatsapp),
        email: email.trim(),
        password,
      });
      navigate("/", { replace: true });
    } catch (err) {
      // Vague message for credential failures (mirrors the backend — never leak
      // which field was wrong); distinct message for a network/CORS failure.
      if (err instanceof ApiError && err.status === 0) {
        setFormError(t.networkError);
      } else {
        setFormError(t.invalidCredentials);
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-dvh items-center justify-center bg-background px-4">
      <div className="w-full max-w-md">
        <div className="mb-6 text-center">
          <h1 className="font-sans text-4xl font-bold text-primary">
            {t.appName}
          </h1>
          <p className="mt-1 text-muted-foreground">{t.loginSubtitle}</p>
        </div>

        <form
          onSubmit={onSubmit}
          noValidate
          className="flex flex-col gap-4 rounded-2xl border border-border bg-card p-6 shadow-sm"
        >
          <h2 className="text-lg font-semibold text-foreground">
            {t.loginTitle}
          </h2>

          <Field
            name="whatsapp_number"
            label={t.whatsappNumber}
            type="tel"
            inputMode="tel"
            autoComplete="tel"
            dir="ltr"
            value={whatsapp}
            onChange={(e) => setWhatsapp(e.target.value)}
            onBlur={() => setTouched((s) => ({ ...s, whatsapp_number: true }))}
            error={fieldError(whatsapp, touched.whatsapp_number)}
            disabled={submitting}
          />
          <Field
            name="email"
            label={t.email}
            type="email"
            inputMode="email"
            autoComplete="email"
            dir="ltr"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onBlur={() => setTouched((s) => ({ ...s, email: true }))}
            error={fieldError(email, touched.email)}
            disabled={submitting}
          />
          <Field
            name="password"
            label={t.password}
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onBlur={() => setTouched((s) => ({ ...s, password: true }))}
            error={fieldError(password, touched.password)}
            disabled={submitting}
          />

          {formError && (
            <p role="alert" className="text-sm text-destructive">
              {formError}
            </p>
          )}

          <Button type="submit" loading={submitting}>
            {submitting ? t.loggingIn : t.loginButton}
          </Button>

          <Link to="/signup" className="text-center text-sm text-primary">
            {t.signupCta}
          </Link>
        </form>
      </div>
    </main>
  );
}
