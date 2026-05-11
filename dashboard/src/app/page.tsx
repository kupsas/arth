import { redirect } from "next/navigation"

/**
 * App root — default landing is Ask Arth (`/chat`). Charts and trends live at `/expense-trends`.
 */
export default function RootPage() {
  redirect("/chat")
}
