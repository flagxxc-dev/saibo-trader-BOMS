import { PrismaClient } from '@prisma/client'
import bcrypt from 'bcryptjs'

const prisma = new PrismaClient()

async function main() {
  const hashedPassword = await bcrypt.hash('admin', 10)
  await prisma.user.upsert({
    where: { email: 'admin' },
    update: {},
    create: { email: 'admin', password: hashedPassword },
  })
  console.log('Default account ready: admin')
}

main()
  .then(async () => { await prisma.$disconnect() })
  .catch(async (e) => {
    console.error(e)
    await prisma.$disconnect()
    process.exit(1)
  })
