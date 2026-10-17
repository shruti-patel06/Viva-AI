"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { currentRole, supabase } from "./supabase";

// The signed-in teacher's email, or null while checking. Anyone else is sent to /login.
export function useTeacher(): string | null {
  const router = useRouter();
  const [email, setEmail] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      if ((await currentRole()) !== "teacher") {
        router.replace("/login");
        return;
      }
      const { data } = await supabase.auth.getSession();
      setEmail(data.session?.user.email ?? "");
    })();
  }, [router]);

  return email;
}
