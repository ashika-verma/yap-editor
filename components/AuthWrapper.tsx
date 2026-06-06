"use client";

import { AuthProvider } from "@/lib/authContext";

export function AuthWrapper({ children }: { children: React.ReactNode }) {
  return <AuthProvider>{children}</AuthProvider>;
}
