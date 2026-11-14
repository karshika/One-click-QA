import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";
import { ThemeToggle } from "@/components/ThemeToggle";
import "./globals.css";

export const metadata: Metadata = {
  title: "OneClickQA",
  description: "One-click QA for small teams: crawl, test, and get a plain-English bug report.",
};

// Applies the saved/system theme before paint to avoid a flash.
const themeScript = `(function(){try{var t=localStorage.getItem('ocqa-theme');var d=t?t==='dark':window.matchMedia('(prefers-color-scheme: dark)').matches;if(d)document.documentElement.classList.add('dark');}catch(e){}})();`;

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-screen">
        <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 rounded bg-surface px-3 py-2">
          Skip to content
        </a>
        <header className="border-b border-border bg-surface">
          <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3">
            <Link href="/" className="flex items-center gap-2 font-semibold text-text">
              <span aria-hidden="true" className="inline-flex h-7 w-7 items-center justify-center rounded-md bg-accent text-sm font-bold text-accent-contrast">
                QA
              </span>
              OneClickQA
            </Link>
            <nav aria-label="Main" className="flex items-center gap-1 text-sm">
              <Link href="/" className="rounded-md px-3 py-2 text-muted hover:bg-surface-2 hover:text-text">
                Projects
              </Link>
              <Link href="/settings" className="rounded-md px-3 py-2 text-muted hover:bg-surface-2 hover:text-text">
                Settings
              </Link>
              <ThemeToggle />
            </nav>
          </div>
        </header>
        <main id="main" className="mx-auto max-w-7xl px-4 py-6">
          {children}
        </main>
        <footer className="mx-auto max-w-7xl px-4 pb-8 text-xs text-muted">
          Only test sites you own or have permission to test. The free Mistral tier may use API data for training,
          so only test staging/demo apps without real customer data.
        </footer>
      </body>
    </html>
  );
}
