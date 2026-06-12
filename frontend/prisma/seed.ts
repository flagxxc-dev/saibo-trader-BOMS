import { PrismaClient } from "@prisma/client";
import bcrypt from "bcryptjs";

const prisma = new PrismaClient();

async function main() {
  const username = process.env.AUTH_USERNAME?.trim() || "admin";
  const password = process.env.AUTH_PASSWORD?.trim() || "change-me-in-production";
  const hashedPassword = await bcrypt.hash(password, 10);

  await prisma.user.upsert({
    where: { email: username },
    update: { password: hashedPassword },
    create: { email: username, password: hashedPassword },
  });

  console.log(`Default account ready: ${username}`);
}

main()
  .then(async () => {
    await prisma.$disconnect();
  })
  .catch(async (e) => {
    console.error(e);
    await prisma.$disconnect();
    process.exit(1);
  });
