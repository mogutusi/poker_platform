/** @type {import('next').NextConfig} */
const nextConfig = {
  // 浏览器测试从 127.0.0.1 访问,而 dev server 认的是 localhost,两者是不同的源。
  // 不声明的话 Next 未来的大版本会直接拒掉 /_next/* 请求。
  allowedDevOrigins: ['127.0.0.1'],
  images: {
    // 页面里用了 quality={90} 的图;Next 16 起未在此声明的质量档会报错,提前配好。
    qualities: [75, 90],
  },
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000',
  },
};

module.exports = nextConfig;
