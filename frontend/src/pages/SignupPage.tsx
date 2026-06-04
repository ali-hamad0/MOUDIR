import { type FormEvent, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "../api/client";
import { signupApi } from "../api/signup";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { t } from "../i18n";

export default function SignupPage() {
  const [businessName, setBusinessName] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [touched, setTouched] = useState({ business: false, phone: false, email: false });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const req = (v: string, was: boolean) => (was && v.trim() === "" ? t.required : null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setTouched({ business: true, phone: true, email: true });
    if (!businessName.trim() || !phone.trim() || !email.trim()) return;
    setSubmitting(true);
    try {
      await signupApi.submit({
        business_name: businessName.trim(),
        owner_phone: phone.trim(),
        owner_email: email.trim(),
      });
      setDone(true);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setError(t.signupDuplicate);
      else if (err instanceof ApiError && err.status === 0) setError(t.networkError);
      else setError(t.signupError);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-dvh items-center justify-center bg-background px-4 py-8">
      <div className="w-full max-w-md">
        <div className="mb-6 text-center">
          <h1 className="font-sans text-3xl font-bold text-primary">{t.appName}</h1>
          <p className="mt-1 text-muted-foreground">{t.tagline}</p>
        </div>

        {done ? (
          <div className="rounded-2xl border border-accent/30 bg-accent/5 p-6 text-center">
            <h2 className="text-lg font-semibold text-foreground">
              {t.signupSuccessTitle}
            </h2>
            <p className="mt-2 text-sm text-muted-foreground">{t.signupSuccessBody}</p>
            <Link
              to="/login"
              className="mt-4 inline-block text-sm font-medium text-primary"
            >
              {t.signupHaveAccount}
            </Link>
          </div>
        ) : (
          <form
            onSubmit={onSubmit}
            noValidate
            className="flex flex-col gap-4 rounded-2xl border border-border bg-card p-6 shadow-sm"
          >
            <div>
              <h2 className="text-lg font-semibold text-foreground">{t.signupTitle}</h2>
              <p className="mt-1 text-sm text-muted-foreground">{t.signupSubtitle}</p>
            </div>
            <Field
              name="business_name"
              label={t.signupBusinessName}
              value={businessName}
              onChange={(e) => setBusinessName(e.target.value)}
              onBlur={() => setTouched((s) => ({ ...s, business: true }))}
              error={req(businessName, touched.business)}
              disabled={submitting}
            />
            <Field
              name="owner_phone"
              label={t.signupOwnerPhone}
              type="tel"
              inputMode="tel"
              autoComplete="tel"
              dir="ltr"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              onBlur={() => setTouched((s) => ({ ...s, phone: true }))}
              error={req(phone, touched.phone)}
              disabled={submitting}
            />
            <Field
              name="owner_email"
              label={t.signupEmail}
              type="email"
              inputMode="email"
              autoComplete="email"
              dir="ltr"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              onBlur={() => setTouched((s) => ({ ...s, email: true }))}
              error={req(email, touched.email)}
              disabled={submitting}
            />
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
            <Button type="submit" loading={submitting}>
              {submitting ? t.signupSubmitting : t.signupSubmit}
            </Button>
            <Link to="/login" className="text-center text-sm text-muted-foreground">
              {t.signupHaveAccount}
            </Link>
          </form>
        )}
      </div>
    </main>
  );
}
