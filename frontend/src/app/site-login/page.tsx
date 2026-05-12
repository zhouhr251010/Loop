import { Suspense } from "react";
import { SiteLoginForm } from "./SiteLoginForm";

export default function SiteLoginPage() {
  return (
    <Suspense
      fallback={
        <main className="min-h-[calc(100vh-65px)] bg-[#f7f7f4] px-4 py-12 text-gray-950">
          <section className="mx-auto h-96 w-full max-w-md rounded-lg border border-gray-200 bg-white" />
        </main>
      }
    >
      <SiteLoginForm />
    </Suspense>
  );
}
