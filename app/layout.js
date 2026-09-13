import "./globals.css";

export const metadata = {
  title: "ToolStack AI",
  description: "AI Video Workspace",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body className="antialiased bg-[#0A0D14] text-slate-100">
        {children}
      </body>
    </html>
  );
}
