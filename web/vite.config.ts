import { defineConfig, loadEnv } from "vite";
import vue from "@vitejs/plugin-vue";
import { resolve } from "path";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, __dirname, "");
  const apiTarget = process.env.VITE_API_TARGET || env.VITE_API_TARGET || "http://127.0.0.1:8000";

  return {
    plugins: [vue()],
    resolve: {
      alias: {
        "@": resolve(__dirname, "src"),
      },
    },
    server: {
      port: 5173,
      watch: {
        // 忽略编辑器/工具保存时产生的瞬态临时目录(目录名以点开头; Windows 路径分隔符是 \\, 必须同时匹配 / 和 \\)
        ignored: [/[\\/]\..*\.tmpdir[\\/]/, /\.tmp$/],
      },
      proxy: {
        "/api": {
          target: apiTarget,
          changeOrigin: true,
        },
        "/health": {
          target: apiTarget,
          changeOrigin: true,
        },
      },
    },
    build: {
      sourcemap: false,
      chunkSizeWarningLimit: 600,
      rollupOptions: {
        output: {
          manualChunks: {
            "element-icons": ["@element-plus/icons-vue"],
            vendor: ["vue", "vue-router", "pinia", "axios"],
            presentation: ["pptxgenjs"],
            scientific: ["katex", "marked"],
          },
        },
      },
    },
  };
});
