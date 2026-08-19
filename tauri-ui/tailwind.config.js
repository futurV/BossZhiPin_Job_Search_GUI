/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          '"Segoe UI Variable"', '"Segoe UI"', '"Microsoft YaHei UI"',
          '"Microsoft YaHei"', '"PingFang SC"', '"Noto Sans CJK SC"', 'sans-serif',
        ],
        serif: [
          '"Segoe UI Variable"', '"Segoe UI"', '"Microsoft YaHei UI"',
          '"Microsoft YaHei"', '"PingFang SC"', 'sans-serif',
        ],
        // 等宽（labels / 数据 / 日志）
        mono: [
          '"Cascadia Mono"', '"Cascadia Code"', "Consolas",
          '"Microsoft YaHei UI"', "monospace",
        ],
      },
      colors: {
        // 单色变量（在 index.css 里定义为 CSS var）
        ink: "var(--ink)",
        paper: "var(--paper)",
        muted: "var(--muted)",
        "muted-fg": "var(--muted-fg)",
        "border-light": "var(--border-light)",
      },
      letterSpacing: {
        // 配合 uppercase 小标签使用
        widest: "0.08em",
      },
    },
  },
  plugins: [],
};
