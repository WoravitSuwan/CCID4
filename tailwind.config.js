/** Tailwind is compiled once into static/css/app.css (committed) so the
 *  system never needs internet access at runtime.
 *  Rebuild after changing templates:
 *    npm install tailwindcss@3.4.17 @tailwindcss/forms@0.5.9
 *    npx tailwindcss -c tailwind.config.js -i static/src/app.css -o static/css/app.css --minify
 */
module.exports = {
  content: ["./templates/**/*.html", "./apps/**/*.py", "./static/js/ui.js"],
  theme: { extend: {} },
  plugins: [require("@tailwindcss/forms")],
};
