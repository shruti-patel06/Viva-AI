"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";

const LINKS = [
  { href: "/teacher", label: "Lab material" },
  { href: "/teacher/questions", label: "Question pool" },
];

export function TeacherHeader({ email }: { email: string | null }) {
  const router = useRouter();
  const pathname = usePathname();

  async function signOut() {
    await supabase.auth.signOut();
    router.replace("/login");
  }

  return (
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-200 pb-4">
      <nav className="flex gap-1">
        {LINKS.map((link) => (
          <Link
            key={link.href}
            href={link.href}
            className={`rounded-md px-3 py-1.5 text-sm ${
              pathname === link.href ? "bg-zinc-900 text-white" : "text-zinc-600 hover:bg-zinc-100"
            }`}
          >
            {link.label}
          </Link>
        ))}
      </nav>
      <div className="flex items-center gap-3 text-sm text-zinc-500">
        <span>{email}</span>
        <button onClick={signOut} className="text-zinc-600 underline">
          Sign out
        </button>
      </div>
    </header>
  );
}
