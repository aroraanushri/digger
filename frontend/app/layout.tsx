import type { Metadata } from "next";
import "@copilotkit/react-core/v2/styles.css";
import "./globals.css";

export const metadata: Metadata = {title: "Digger · Asset intelligence", description: "Operational answers. Traceable evidence."};
export default function Layout({children}: {children: React.ReactNode}) {
  return <html lang="en"><body>{children}</body></html>;
}
