"use client";
import { useParams } from "next/navigation";
import { useStore } from "@/lib/store";
import { ProblemView, NotFound } from "@/components/app";
import { stubContest } from "@/lib/mock";

export default function Page() {
  const { cid, kind } = useParams<{ cid: string; kind: string }>();
  const { apiMode, contests } = useStore();
  // API mode fetches the problem by id (ApiProblemView), so deep-links resolve without
  // the contest being present in the cached list.
  const c = contests.find((x) => x.id === cid) ?? (apiMode ? stubContest(cid) : undefined);
  if (!c || (kind !== "stepup" && kind !== "challenge")) return <NotFound />;
  return <ProblemView contest={c} kind={kind} />;
}
