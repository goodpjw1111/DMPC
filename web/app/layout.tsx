import "./globals.css";
import "katex/dist/katex.min.css";
import type { ReactNode } from "react";
import { StoreProvider } from "@/lib/store";
import { Shell } from "@/components/app";

export const metadata = {
  title: "DMPC — 디미고 모의 프로그래밍 콘테스트",
  description: "NYPC Rookie 대비 휴리스틱 채점 플랫폼",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ko">
      <body>
        <StoreProvider>
          <Shell>{children}</Shell>
        </StoreProvider>
      </body>
    </html>
  );
}
