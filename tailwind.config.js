/** Tailwind is compiled once into static/css/app.css (committed) so the
 *  system never needs internet access at runtime.
 *  Rebuild after changing templates:
 *    npm install tailwindcss@3.4.17 @tailwindcss/forms@0.5.9
 *    npx tailwindcss -c tailwind.config.js -i static/src/app.css -o static/css/app.css --minify
 *
 *  CCID4 theme: "indigo" (used across the templates for the main colour) is redefined as a deep police navy,
 *  so every button, link and highlight follows without touching the templates; "gold" is the accent of the
 *  emblem, kept for what matters (the active menu item, the brand, key figures).
 */
module.exports = {
  content: ["./templates/**/*.html", "./apps/**/*.py", "./static/js/ui.js"],
  theme: {
    extend: {
      colors: {
        indigo: {
          50: "#eef3fb", 100: "#dbe5f5", 200: "#b8cbeb", 300: "#8eaadc", 400: "#5f84c8", 500: "#3a64b1",
          600: "#1f4b9a", 700: "#1a3d7e", 800: "#163266", 900: "#122852", 950: "#0b1a36",
        },
        gold: {
          50: "#fdf9ec", 100: "#faefc9", 200: "#f4dd8f", 300: "#ecc659", 400: "#e3b034", 500: "#c99a1f",
          600: "#a67a16", 700: "#805c14", 800: "#5f4513", 900: "#3f2e0d",
        },
      },
    },
  },
  plugins: [require("@tailwindcss/forms")],
};
