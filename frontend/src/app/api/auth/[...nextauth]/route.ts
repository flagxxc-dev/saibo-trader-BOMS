import NextAuth, { NextAuthOptions } from "next-auth";
import CredentialsProvider from "next-auth/providers/credentials";
import { prisma } from "@/lib/prisma";
import bcrypt from "bcryptjs";

async function verifyCredentials(username: string, password: string) {
  const user = await prisma.user.findUnique({
    where: { email: username },
  });

  if (user) {
    const ok = await bcrypt.compare(password, user.password);
    if (ok) return { id: user.id, email: user.email };
  }

  const envUser = process.env.AUTH_USERNAME?.trim();
  const envPass = process.env.AUTH_PASSWORD?.trim();
  if (envUser && envPass && username === envUser && password === envPass) {
    return { id: "env-admin", email: envUser };
  }

  return null;
}

export const authOptions: NextAuthOptions = {
  providers: [
    CredentialsProvider({
      name: "Credentials",
      credentials: {
        username: { label: "账号", type: "text" },
        password: { label: "密码", type: "password" },
      },
      async authorize(credentials) {
        const username = credentials?.username?.trim();
        const password = credentials?.password ?? "";
        if (!username || !password) return null;
        return verifyCredentials(username, password);
      },
    }),
  ],
  session: {
    strategy: "jwt",
    maxAge: 30 * 24 * 60 * 60,
  },
  pages: {
    signIn: "/login",
  },
  callbacks: {
    async jwt({ token, user }) {
      if (user) {
        token.id = user.id;
      }
      return token;
    },
    async session({ session, token }) {
      if (token && session.user) {
        session.user.email = token.email;
        // @ts-expect-error extended session user
        session.user.id = token.id;
      }
      return session;
    },
  },
  secret: process.env.NEXTAUTH_SECRET,
};

const handler = NextAuth(authOptions);

export { handler as GET, handler as POST };
