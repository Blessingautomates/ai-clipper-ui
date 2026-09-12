import "./globals.css";

export const metadata = {
  title: "ToolStack Clipper - Turn YouTube Videos into Viral Shorts",
  description: "Automated AI clipping engine to turn long-form videos into high-engagement short videos.",
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
