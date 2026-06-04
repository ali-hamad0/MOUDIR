import { API_BASE_URL } from "../config";
import { ApiError } from "./client";

// Public signup-request submission (no auth). Creates a PENDING request only —
// no account, no login until a founder approves.

export interface SignupRequestInput {
  business_name: string;
  owner_phone: string;
  owner_email: string;
}

export interface SignupRequestResult {
  id: string;
  business_name: string;
  owner_email: string;
  status: string;
  created_at: string;
}

export const signupApi = {
  submit: async (input: SignupRequestInput): Promise<SignupRequestResult> => {
    let res: Response;
    try {
      res = await fetch(`${API_BASE_URL}/signup-requests`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
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
    return (await res.json()) as SignupRequestResult;
  },
};
