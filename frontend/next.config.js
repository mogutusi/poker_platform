/** @type {import('next').NextConfig} */
const nextConfig = {
  images: {
    // 页面里用了 quality={90} 的图;Next 16 起未在此声明的质量档会报错,提前配好。
    qualities: [75, 90],
  },
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000',
  },
};

module.exports = nextConfig;
