import { API_BASE_URL } from "../config";
import { ApiError } from "./client";

// Public signup-request submission (no auth). Creates a PENDING request only —
// no account, no login until a founder approves.

export interface SignupRequestInput {
  business_name: string;
  owner_phone: string;
  owner_email: string;
  otp_code: string;
}

export interface SignupRequestResult {
  id: string;
  business_name: string;
  owner_email: string;
  status: string;
  created_at: string;
}

export interface OtpResult {
  sent_to: string;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      if (typeof data?.detail === "string") detail = data.detail;
    } catch {
      /* keep status text */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export const signupApi = {
  // Step 1: WhatsApp a one-time code to the phone so we can prove it's real.
  requestOtp: (owner_phone: string): Promise<OtpResult> =>
    postJson<OtpResult>("/signup-requests/otp", { owner_phone }),

  // Step 2: submit the application with the code the owner received.
  submit: (input: SignupRequestInput): Promise<SignupRequestResult> =>
    postJson<SignupRequestResult>("/signup-requests", input),
};
