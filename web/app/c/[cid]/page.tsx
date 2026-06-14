"use client";
import { useParams } from "next/navigation";
import { useStore } from "@/lib/store";
import { ContestDetailView, NotFound } from "@/components/app";
import { stubContest } from "@/lib/mock";

export default function Page() {
  const { cid } = useParams<{ cid: string }>();
  const { apiMode, contests } = useStore();
  // API mode resolves the contest by id via fetch (ApiContestDetail), so a deep-link
  // or just-created contest not yet in the cached list still loads instead of 404ing.
  const c = contests.find((x) => x.id === cid) ?? (apiMode ? stubContest(cid) : undefined);
  return c ? <ContestDetailView contest={c} /> : <NotFound />;
}
