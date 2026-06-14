import { redirect } from "next/navigation";

// Legacy OAuth callback landing — the app now lives at "/". (Real-API mode can
// point GOOGLE redirect back to "/" instead.)
export default function Page() {
  redirect("/");
}
