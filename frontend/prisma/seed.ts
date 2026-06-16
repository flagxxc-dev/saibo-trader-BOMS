import { PrismaClient } from "@prisma/client";
import bcrypt from "bcryptjs";

const prisma = new PrismaClient();

async function main() {
  const username = process.env.AUTH_USERNAME?.trim();
  const password = process.env.AUTH_PASSWORD?.trim();
  if (!username || !password) {
    throw new Error("AUTH_USERNAME and AUTH_PASSWORD must be set before seeding");
  }
  const hashedPassword = await bcrypt.hash(password, 10);

  await prisma.user.deleteMany({
    where: { email: { not: username } },
  });

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
