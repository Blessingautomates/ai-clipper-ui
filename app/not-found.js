import Link from "next/link";

export default function NotFound() {
  return (
    <div className="min-h-screen bg-[#0A0D14] text-white flex flex-col items-center justify-center p-4 text-center space-y-4">
      <h1 className="text-4xl font-bold">404 - Page Not Found</h1>
      <p className="text-slate-400 text-sm">The page you are looking for does not exist.</p>
      <Link href="/" className="bg-blue-600 text-white px-4 py-2 rounded-xl text-sm font-medium">
        Go Back Home
      </Link>
    </div>
  );
}
