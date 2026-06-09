import { API_BASE_URL } from "../config";

export interface AIHealth {
  available: boolean;
}

export async function fetchAIHealth(): Promise<AIHealth> {
  try {
    const res = await fetch(`${API_BASE_URL}/health/ai`);
    if (!res.ok) return { available: false };
    return (await res.json()) as AIHealth;
  } catch {
    return { available: false };
  }
}
