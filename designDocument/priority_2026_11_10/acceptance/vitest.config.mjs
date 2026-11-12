import { defineConfig } from "../../../frontend/node_modules/vitest/dist/config.js";
import react from "../../../frontend/node_modules/@vitejs/plugin-react/dist/index.js";
import { fileURLToPath } from "node:url";

const modules = fileURLToPath(new URL("../../../frontend/node_modules/", import.meta.url));
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      react: modules + "react",
      "react-dom": modules + "react-dom",
      vitest: modules + "vitest",
      "@testing-library/react": modules + "@testing-library/react",
      "@testing-library/user-event": modules + "@testing-library/user-event",
    },
  },
  test: {
    environment: "jsdom",
    include: ["designDocument/priority_2026_11_10/acceptance/test_team_app.tsx"],
  },
});
